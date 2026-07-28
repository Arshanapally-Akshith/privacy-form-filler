"""Privacy mode selection (DECISIONS.md V1, BUILD.md Phase 4 task 2).

Defined here, not in app.extraction or app.api, because the mode governs boundary
behavior specifically -- which of protect()/reverse() get invoked around an outbound LLM
call. Both the extraction call site (BUILD.md Phase 4 commit 4) and the eventual API
request parameter (a later commit) import this same enum rather than each defining their
own.
"""

from enum import Enum


class PrivacyMode(str, Enum):
    NONE = "none"
    FULL_TOKENIZE = "full_tokenize"
    POLICY_ENGINE = "policy_engine"
