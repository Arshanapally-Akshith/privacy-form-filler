"""Query construction from field labels (BUILD.md Phase 1, task 5).

Deliberately minimal: a label plus lightweight, static synonym expansion. No LLM query
rewriting, no semantic expansion model, no form schema dependency -- "the verifier handles
ambiguity later" (BUILD.md Phase 5). build_query_text() is pure and deterministic; it does
not call the embedder itself. Whatever calls it is expected to pass the resulting text to
the existing app.retrieval.embedder.embed_texts() -- there is no second embedding pathway.

DECISIONS.md E16: this synonym table is a starting point, not tuned. Recall@k (BUILD.md
Phase 1 tasks 7-8) is what will validate it.
"""

# DECISIONS.md E16 -- provisional. Each inner list is a group of interchangeable phrases;
# groups are seeded from the entity types already named in DECISIONS.md P12.
FIELD_LABEL_SYNONYM_GROUPS: list[list[str]] = [
    ["pan", "permanent account number"],
    ["aadhaar", "aadhar", "unique identification number", "uid"],
    ["dob", "date of birth", "birth date"],
    ["passport", "passport number"],
    ["phone", "mobile number", "contact number", "telephone number"],
    ["email", "email address"],
    ["address", "residential address", "permanent address"],
    ["pin code", "pincode", "postal code", "zip code"],
    ["account number", "bank account number"],
    ["name", "full name", "applicant name"],
]


def build_query_text(label: str) -> str:
    normalized_label = label.lower()
    expansions: list[str] = []
    for group in FIELD_LABEL_SYNONYM_GROUPS:
        if not any(phrase in normalized_label for phrase in group):
            continue
        for phrase in group:
            if phrase not in normalized_label and phrase not in expansions:
                expansions.append(phrase)

    if not expansions:
        return label
    return label + " " + " ".join(expansions)
