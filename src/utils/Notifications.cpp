#include "utils/Notifications.h"
#include <imgui.h>
#include <spdlog/spdlog.h>

namespace vbt {

Notifications& Notifications::get() {
    static Notifications inst;
    return inst;
}

void Notifications::push(ToastLevel level, std::string text, float lifetime_s) {
    Toast t;
    t.level = level;
    t.text = std::move(text);
    t.created = std::chrono::steady_clock::now();
    t.lifetime_s = lifetime_s;

    {
        std::lock_guard<std::mutex> lock(mu_);
        active_.push_back(t);
        history_.push_back(t);
        while (history_.size() > kMaxHistory) history_.pop_front();
    }

    switch (level) {
        case ToastLevel::Error:   spdlog::error("[toast] {}",   t.text); break;
        case ToastLevel::Warning: spdlog::warn ("[toast] {}",   t.text); break;
        case ToastLevel::Success: spdlog::info ("[toast] {}",   t.text); break;
        case ToastLevel::Info:    spdlog::info ("[toast] {}",   t.text); break;
    }
}

void Notifications::render() {
    std::deque<Toast> to_draw;
    auto now = std::chrono::steady_clock::now();
    {
        std::lock_guard<std::mutex> lock(mu_);
        // expire
        while (!active_.empty()) {
            float age = std::chrono::duration<float>(now - active_.front().created).count();
            if (age > active_.front().lifetime_s) active_.pop_front();
            else break;
        }
        to_draw = active_;
    }
    if (to_draw.empty()) return;

    const ImGuiViewport* vp = ImGui::GetMainViewport();
    float padding = 14.0f;
    float toast_w = 360.0f;
    float y = vp->WorkPos.y + vp->WorkSize.y - padding;
    int idx = (int)to_draw.size() - 1;
    for (auto it = to_draw.rbegin(); it != to_draw.rend(); ++it, --idx) {
        const Toast& t = *it;
        float age = std::chrono::duration<float>(now - t.created).count();
        float fade = 1.0f;
        if (age > t.lifetime_s - 0.6f) fade = (t.lifetime_s - age) / 0.6f;
        if (fade < 0) fade = 0;

        ImVec4 colA, colB;
        const char* icon;
        switch (t.level) {
            case ToastLevel::Error:   colA = {0.85f,0.20f,0.18f,0.95f*fade}; colB = {1,1,1,1*fade}; icon = "[ERR]";  break;
            case ToastLevel::Warning: colA = {0.85f,0.55f,0.10f,0.95f*fade}; colB = {1,1,1,1*fade}; icon = "[!]";    break;
            case ToastLevel::Success: colA = {0.20f,0.65f,0.30f,0.95f*fade}; colB = {1,1,1,1*fade}; icon = "[OK]";   break;
            case ToastLevel::Info:    colA = {0.20f,0.40f,0.70f,0.95f*fade}; colB = {1,1,1,1*fade}; icon = "[i]";    break;
        }

        ImGui::SetNextWindowSize(ImVec2(toast_w, 0), ImGuiCond_Always);
        ImGui::SetNextWindowPos(ImVec2(vp->WorkPos.x + vp->WorkSize.x - padding - toast_w, y),
                                ImGuiCond_Always, ImVec2(0,1));
        ImGui::SetNextWindowBgAlpha(0.92f * fade);
        ImGui::PushStyleColor(ImGuiCol_WindowBg, colA);
        ImGui::PushStyleColor(ImGuiCol_Text, colB);
        char id[64]; snprintf(id, sizeof(id), "##toast_%d_%lld", idx, (long long)t.created.time_since_epoch().count());
        ImGui::Begin(id, nullptr,
            ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoNav |
            ImGuiWindowFlags_NoFocusOnAppearing | ImGuiWindowFlags_NoSavedSettings |
            ImGuiWindowFlags_AlwaysAutoResize);
        ImGui::TextWrapped("%s %s", icon, t.text.c_str());
        ImGui::End();
        ImGui::PopStyleColor(2);

        y -= ImGui::GetItemRectSize().y + 6;
        // safety: limit number visible
        if (idx < 0) break;
    }
}

std::deque<Toast> Notifications::snapshot() const {
    std::lock_guard<std::mutex> lock(mu_);
    return history_;
}

void Notifications::clear() {
    std::lock_guard<std::mutex> lock(mu_);
    active_.clear();
    history_.clear();
}

} // namespace vbt
