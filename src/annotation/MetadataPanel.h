#pragma once

/**
 * @file MetadataPanel.h
 * @brief Editor for the session's SessionInfo (metadata.json).
 *
 * Thin wrapper over gui/SessionInfoForm — that header is shared with
 * the recording-side SessionPanel so both surfaces edit the same
 * struct with the same widgets. We only contribute dirty-flag
 * book-keeping and the "● unsaved changes" header strip.
 */

#include "annotation/SessionData.h"
#include <imgui.h>

namespace vbt {

class MetadataPanel {
public:
    MetadataPanel() = default;
    void set_session(SessionData* s) { session_ = s; }
    void render(bool* visible = nullptr);

private:
    bool         expand_all_ = false;
    SessionData* session_    = nullptr;
};

} // namespace vbt
