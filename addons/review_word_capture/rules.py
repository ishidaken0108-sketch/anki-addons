"""Advanced per-source-deck routing rules for Review Word Capture.

The normal add-on settings remain the fallback.  Rules here only override the
source decks/colon branches that are explicitly listed.

When the user wants a new special case, edit ADVANCED_RULES and ship a new zip.
"""

ADVANCED_RULES = [
    # Example (disabled):
    # {
    #     "name": "Vocabulary source deck",
    #     "source_decks": ["English::Reading"],
    #     "include_subdecks": True,
    #
    #     # Used when the selected text contains ':' or '：'.
    #     "with_colon": {
    #         "destination_deck": "English::Vocabulary::Meaning",
    #         "mnemonics": [
    #             {"field": "Content", "mode": "first_image"},
    #             {"field": "Note", "mode": "first_image"},
    #         ],
    #         "mnemonics_separator": "<br><br>",
    #     },
    #
    #     # Used when the selected text does not contain ':' or '：'.
    #     "without_colon": {
    #         "destination_deck": "English::Vocabulary::Word",
    #         "mnemonics": [],
    #     },
    # },
]
