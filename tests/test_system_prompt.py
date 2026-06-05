import os
import sys
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from AgenticGis.backends.openai_backend import DEFAULT_SYSTEM_PROMPT


def _extract_followup_paragraph(prompt):
    """Return the 'After the analysis…' paragraph (and the blank line after it)."""
    lines = prompt.split("\n")
    capture = False
    captured = []
    for line in lines:
        if "After the analysis" in line:
            capture = True
        if capture:
            captured.append(line)
            if line.strip() == "" and len(captured) > 1:
                break
    return "\n".join(captured)


class TestFollowupGuidance(unittest.TestCase):
    def test_followup_uses_ask_user_not_text_sentence(self):
        # The old instruction told the model to write a follow-up
        # sentence in text. The new instruction must reference the
        # existing ask_user tool so the chatbox actually shows the
        # question card.
        self.assertNotIn(
            "end with one sentence suggesting",
            DEFAULT_SYSTEM_PROMPT,
            "System prompt still tells the model to write a follow-up "
            "as a text sentence; it should call ask_user instead.",
        )

    def test_followup_paragraph_mentions_ask_user(self):
        para = _extract_followup_paragraph(DEFAULT_SYSTEM_PROMPT)
        self.assertIn(
            "ask_user",
            para,
            f"The 'After the analysis' paragraph should reference "
            f"ask_user, got: {para!r}",
        )

    def test_followup_paragraph_options_count_in_range(self):
        # ask_user requires 2-4 options per the tool spec. The
        # follow-up instruction must respect that bound.
        para = _extract_followup_paragraph(DEFAULT_SYSTEM_PROMPT).lower()
        self.assertTrue(
            ("2-4" in para) or ("2 to 4" in para) or ("2 and 4" in para),
            f"Follow-up guidance should specify a 2-4 option range "
            f"to match ask_user's input schema, got: {para!r}",
        )

    def test_prompt_still_has_output_style_section(self):
        # Guard against accidental damage to surrounding sections.
        self.assertIn("## Output style", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("## Tools", DEFAULT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
