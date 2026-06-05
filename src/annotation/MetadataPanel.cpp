/**
 * @file MetadataPanel.cpp
 *
 * Annotation-studio side of the SessionInfo editor. Uses the shared
 * SessionInfoForm so the recording panel and the studio show the same
 * widget set; we just translate "form mutated" → meta_dirty.
 */
#include "annotation/MetadataPanel.h"
#include "gui/SessionInfoForm.h"

namespace vbt {

void MetadataPanel::render(bool* visible) {
    // Header strip — title, dirty indicator, expand-all toggle.
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.30f, 0.78f, 1.0f, 1.0f));
    ImGui::TextUnformatted("METADATA");
    ImGui::PopStyleColor();
    ImGui::SameLine();
    if (visible && ImGui::SmallButton("Hide##metadata_panel")) {
        *visible = false;
    }
    if (visible && ImGui::IsItemHovered()) {
        ImGui::BeginTooltip();
        ImGui::TextUnformatted("Hide the metadata editor and give the workspace more room.");
        ImGui::EndTooltip();
    }

    if (!session_ || !session_->is_loaded()) {
        ImGui::Separator();
        ImGui::TextDisabled("Load a session to edit its metadata.");
        return;
    }

    if (session_->meta_dirty()) {
        ImGui::SameLine();
        ImGui::TextColored(ImVec4(1.0f, 0.85f, 0.30f, 1.0f),
                            "  ●  unsaved (Ctrl+S)");
    } else {
        ImGui::SameLine();
        ImGui::TextDisabled("  (clean)");
    }
    ImGui::Checkbox("Expand all sections", &expand_all_);
    ImGui::SameLine();
    ImGui::TextDisabled("(opens every block at once)");
    ImGui::Separator();

    // The form mutates the session's SessionInfo in place; we just
    // forward the "anything changed?" bit to mark_meta_dirty so the
    // unsaved indicator and Ctrl+S workflow keep working.
    // Studio has no AppConfig handle, but the exercise dropdown is purely a
    // UI affordance — feeding it the built-in default list gives the same
    // selection experience as the recording panel without coupling the
    // studio to runtime config.
    static const auto kProfiles = default_exercise_profiles();
    if (session_info_form::render_full_form(session_->mutable_info(),
                                              session_->t0_unified_s(),
                                              expand_all_,
                                              kProfiles)) {
        session_->mark_meta_dirty();
    }
}

} // namespace vbt
