// SPDX-License-Identifier: MIT
#include "StudioBridge.hpp"
#include "GUI_App.hpp"
#include "MainFrame.hpp"
#include "Plater.hpp"
#include "Tab.hpp"
#include "Jobs/Worker.hpp"
#include "libslic3r/PresetBundle.hpp"
#include <wx/timer.h>
#include <wx/utils.h>
#include <wx/dialog.h>
#include <boost/uuid/random_generator.hpp>
#include <boost/uuid/uuid_io.hpp>
#include <nlohmann/json.hpp>
#include <openssl/sha.h>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <chrono>
#include <memory>

namespace Slic3r { namespace GUI {
namespace {
using json = nlohmann::json;
namespace fs = std::filesystem;
std::string uid() { return boost::uuids::to_string(boost::uuids::random_generator()()); }
std::string digest(const std::string &s) {
    unsigned char bytes[SHA256_DIGEST_LENGTH];
    SHA256(reinterpret_cast<const unsigned char*>(s.data()), s.size(), bytes);
    std::ostringstream out;
    for (auto b : bytes) out << std::hex << std::setw(2) << std::setfill('0') << int(b);
    return out.str();
}
double now() {
    return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
}
void write_json(const fs::path &path, const json &value) {
    const fs::path temp = path.u8string() + ".tmp";
    { std::ofstream out(temp, std::ios::binary | std::ios::trunc); out << value.dump(); out.flush();
      if (!out) throw std::runtime_error("Bridge response write failed"); }
    // Unique response filenames are never replaced. Session metadata is written once.
    fs::rename(temp, path);
}
bool private_key(const std::string &key) {
    for (const char *word : {"password", "token", "apikey", "access_code", "print_host", "printhost"})
        if (key.find(word) != std::string::npos) return true;
    return false;
}
json settings(const DynamicPrintConfig &config) {
    json result = json::object();
    for (const auto &key : config.keys())
        if (!private_key(key)) result[key] = config.opt_serialize(key);
    return result;
}
class Bridge : public wxTimer {
    fs::path directory;
    std::string session = uid(), token = uid() + uid();
    bool handling = false;
    json snapshot() {
        auto *plater = wxGetApp().plater();
        auto *bundle = wxGetApp().preset_bundle;
        if (!plater || !bundle) throw std::runtime_error("Studio project is not ready");
        json state = {{"project_id", std::to_string(plater->model().id().id)},
                      {"filename", plater->get_project_filename().ToUTF8().data()},
                      {"process", settings(bundle->prints.get_edited_preset().config)},
                      {"project", settings(bundle->project_config)},
                      {"printer", settings(bundle->printers.get_edited_preset().config)},
                      {"filaments", json::array()}, {"objects", json::array()}};
        for (size_t i = 0; i < bundle->filament_presets.size(); ++i) {
            auto *preset = bundle->filaments.find_preset(bundle->filament_presets[i], true);
            if (!preset) throw std::runtime_error("Missing filament preset");
            state["filaments"].push_back({{"slot", i + 1}, {"preset", preset->name},
                {"editable", preset->name == bundle->filaments.get_edited_preset().name}, {"settings", settings(preset->config)}});
        }
        for (auto *object : plater->model().objects)
            state["objects"].push_back({{"id", object->id().id}, {"name", object->name}, {"volumes", object->volumes.size()},
                                        {"settings", settings(object->config.get())}});
        state["revision"] = digest(state.dump());
        state["session_id"] = session;
        state["dirty"] = plater->is_project_dirty();
        state["window_active"] = wxGetApp().mainframe->IsActive();
        state["title"] = wxGetApp().mainframe->GetTitle().ToUTF8().data();
        state["busy"] = plater->is_background_process_slicing();
        return state;
    }
    void writable() {
        auto *p = wxGetApp().plater();
        if (!p || p->is_background_process_slicing() || p->is_export_gcode_scheduled() || !p->get_ui_job_worker().is_idle())
            throw std::runtime_error("Studio is busy; no changes applied");
        for (auto *window : wxTopLevelWindows) {
            auto *dialog = dynamic_cast<wxDialog*>(window);
            if (dialog && dialog->IsModal()) throw std::runtime_error("Close the Studio modal dialog before editing");
        }
    }
    std::string checkpoint(const std::string &id, const std::string &suffix) {
        auto file = directory / (id + suffix + ".3mf");
        if (fs::exists(file)) throw std::runtime_error("Checkpoint already exists");
        auto strategy = SaveStrategy::Silence | SaveStrategy::Backup | SaveStrategy::SplitModel | SaveStrategy::ShareMesh;
        if (wxGetApp().plater()->export_3mf(boost::filesystem::path(file.u8string()), strategy) < 0 || !fs::exists(file))
            throw std::runtime_error("Project checkpoint failed; inspect Studio");
        return file.u8string();
    }
    json execute(const json &request, const std::string &id) {
        if (request.at("token") != token || request.at("session_id") != session)
            throw std::runtime_error("Session authentication failed");
        if (request.at("deadline").get<double>() < now()) throw std::runtime_error("Request expired without execution");
        const auto operation = request.at("operation").get<std::string>();
        auto before = snapshot();
        if (operation == "read") return before;
        if (operation != "update" && operation != "checkpoint") throw std::runtime_error("Unsupported operation");
        writable();
        if (request.at("expected_revision") != before.at("revision"))
            throw std::runtime_error("Project/settings changed since read; no changes applied");
        if (operation == "checkpoint") return {{"path", checkpoint(id, "-saved")}, {"snapshot", snapshot()}};
        auto *bundle = wxGetApp().preset_bundle;
        const auto scope = request.at("scope").get<std::string>();
        Preset *preset = nullptr;
        Tab *tab = nullptr;
        if (scope == "process") {
            preset = &bundle->prints.get_edited_preset();
            tab = wxGetApp().get_tab(Preset::TYPE_PRINT);
        } else if (scope == "filament") {
            const auto slot = request.at("filament_slot").get<size_t>();
            if (slot < 1 || slot > bundle->filament_presets.size()) throw std::runtime_error("Invalid filament slot");
            const auto &name = bundle->filament_presets[slot - 1];
            json affected = json::array();
            for (size_t i = 0; i < bundle->filament_presets.size(); ++i)
                if (bundle->filament_presets[i] == name) affected.push_back(i + 1);
            if (affected != request.at("affected_slots")) throw std::runtime_error("Explicit affected_slots must match every slot sharing this preset");
            preset = bundle->filaments.find_preset(name, true);
            if (name != bundle->filaments.get_edited_preset().name)
                throw std::runtime_error("This filament preset is not selected; native writes currently require an editable filament from readback");
            tab = wxGetApp().get_tab(Preset::TYPE_FILAMENT);
        } else throw std::runtime_error("Only process and filament settings can be updated");
        if (!preset) throw std::runtime_error("Preset unavailable");
        auto original = preset->config;
        auto candidate = original;
        const auto changes = request.at("changes");
        if (!changes.is_object() || changes.empty() || changes.size() > 100) throw std::runtime_error("Expected 1 to 100 settings");
        for (auto it = changes.begin(); it != changes.end(); ++it) {
            const auto &key = it.key();
            if (!it.value().is_string() || !candidate.has(key) || private_key(key) || key.find("gcode") != std::string::npos ||
                key == "inherits" || key.find("_settings_id") != std::string::npos || key.find("compatible_") == 0)
                throw std::runtime_error("Unsupported setting key or serialized value: " + key);
            candidate.set_deserialize_strict(key, it.value().get<std::string>());
        }
        auto errors = candidate.validate();
        if (!errors.empty()) throw std::runtime_error("Invalid configuration: " + json(errors).dump());
        const auto backup = checkpoint(id, "-before");
        if (snapshot().at("revision") != before.at("revision"))
            throw std::runtime_error("Project changed during checkpoint; no settings applied");
        wxGetApp().plater()->take_snapshot("Bambu Bridge settings");
        const bool was_dirty = preset->is_dirty;
        try {
            if (tab) tab->load_config(candidate);
            else { preset->config = candidate; preset->is_dirty = true; }
            wxGetApp().plater()->on_config_change(bundle->full_config());
            wxGetApp().plater()->update_project_dirty_from_presets();
            auto after = snapshot();
            const auto actual = scope == "process" ? after["process"] : after["filaments"][request.at("filament_slot").get<size_t>() - 1]["settings"];
            for (auto it = changes.begin(); it != changes.end(); ++it)
                if (actual.at(it.key()) != candidate.opt_serialize(it.key())) throw std::runtime_error("Settings readback mismatch: " + it.key());
            const auto saved = checkpoint(id, "-after");
            return {{"applied", true}, {"open_project_updated", true}, {"checkpoint_before", backup},
                    {"checkpoint_after", saved}, {"snapshot", after}};
        } catch (...) {
            if (tab) tab->load_config(original); else preset->config = original;
            preset->is_dirty = was_dirty;
            wxGetApp().plater()->on_config_change(bundle->full_config());
            wxGetApp().plater()->update_project_dirty_from_presets();
            throw;
        }
    }
public:
    explicit Bridge(const fs::path &root) {
        directory = root / session;
        fs::create_directories(directory / "requests");
        fs::create_directories(directory / "responses");
        fs::permissions(directory, fs::perms::owner_all, fs::perm_options::replace);
        write_json(directory / "session.json", {{"protocol", 1}, {"session_id", session}, {"token", token},
            {"pid", wxGetProcessId()}, {"created_at", now()}, {"backend", "studio-native-wx"}});
        Start(150);
    }
    ~Bridge() { Stop(); std::error_code ec; fs::remove(directory / "session.json", ec); }
    void Notify() override {
        if (handling || !wxGetApp().mainframe || !wxGetApp().plater()) return;
        handling = true;
        try {
            for (const auto &entry : fs::directory_iterator(directory / "requests")) {
                const auto path = entry.path();
                if (path.extension() != ".json" || entry.is_symlink() || !entry.is_regular_file()) continue;
                const auto id = path.stem().string();
                if (id.size() != 36 || id.find_first_not_of("0123456789abcdef-") != std::string::npos) continue;
                const auto response = directory / "responses" / (id + ".json");
                const auto claimed = directory / "requests" / (id + ".claimed");
                if (fs::exists(response) || fs::exists(claimed)) continue;
                // Claim before processing; a crash/unknown result cannot replay a write.
                fs::rename(path, claimed);
                json result;
                try {
                    if (fs::file_size(claimed) > 1024 * 1024) throw std::runtime_error("Request too large");
                    std::ifstream in(claimed); json request; in >> request;
                    result = {{"ok", true}, {"result", execute(request, id)}};
                } catch (const std::exception &e) { result = {{"ok", false}, {"error", e.what()}}; }
                result["request_id"] = id;
                result["session_id"] = session;
                write_json(response, result);
                break; // One request per GUI tick.
            }
        } catch (const std::exception &) { /* Keep the app alive; client reports an unknown timeout. */ }
        handling = false;
    }
};
std::unique_ptr<Bridge> bridge;
}
void start_codex_studio_bridge() {
    wxString root;
    if (!wxGetEnv("BAMBU_BRIDGE_NATIVE_DIR", &root) || root.empty()) return;
    try { bridge = std::make_unique<Bridge>(fs::u8path(root.ToUTF8().data())); } catch (...) { bridge.reset(); }
}
void stop_codex_studio_bridge() { bridge.reset(); }
}}
