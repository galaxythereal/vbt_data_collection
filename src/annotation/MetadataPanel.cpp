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

void MetadataPanel::render() {
    if (!session_ || !session_->is_loaded()) {
        ImGui::TextDisabled("Load a session to edit its metadata.");
        return;
    }

    // Header strip — title, dirty indicator, expand-all toggle.
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.30f, 0.78f, 1.0f, 1.0f));
    ImGui::TextUnformatted("METADATA");
    ImGui::PopStyleColor();
    ImGui::SameLine();
    if (session_->meta_dirty()) {
        ImGui::TextColored(ImVec4(1.0f, 0.85f, 0.30f, 1.0f),
                            "  ●  unsaved (Ctrl+S)");
    } else {
        ImGui::TextDisabled("  (clean)");
    }
    ImGui::Checkbox("Expand all sections", &expand_all_);
    ImGui::SameLine();
    ImGui::TextDisabled("(opens every block at once)");
    ImGui::Separator();

    // The form mutates the session's SessionInfo in place; we just
    // forward the "anything changed?" bit to mark_meta_dirty so the
    // unsaved indicator and Ctrl+S workflow keep working.
    if (session_info_form::render_full_form(session_->mutable_info(),
                                              session_->t0_unified_s(),
                                              expand_all_)) {
        session_->mark_meta_dirty();
    }
}

} // namespace vbt
