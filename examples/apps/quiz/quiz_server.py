"""Quiz / trivia app — a FastMCPApp example with multi-turn state.

Demonstrates building state over a conversation:
- The LLM generates quiz questions and calls `take_quiz` to launch the UI
- The user answers via multiple-choice buttons (no forms)
- Each answer calls `submit_answer`, which returns correctness + updated score
- After the final question, a SendMessage pushes the score back to the LLM

Usage:
    uv run python quiz_server.py
"""

from __future__ import annotations

from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool, SendMessage
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge,
    Button,
    Card,
    Column,
    Heading,
    If,
    Muted,
    Progress,
    Row,
    Text,
)
from prefab_ui.rx import ERROR, RESULT, Rx

from fastmcp import FastMCP, FastMCPApp

app = FastMCPApp("Quiz")

DEFAULT_QUESTIONS = [
    {
        "question": "What is the capital of Australia?",
        "options": ["Sydney", "Melbourne", "Canberra", "Perth"],
        "correct": 2,
    },
    {
        "question": "Which planet has the most moons?",
        "options": ["Jupiter", "Saturn", "Uranus", "Neptune"],
        "correct": 1,
    },
    {
        "question": "What year did the Berlin Wall fall?",
        "options": ["1987", "1989", "1991", "1993"],
        "correct": 1,
    },
    {
        "question": "Which element has the chemical symbol 'Au'?",
        "options": ["Silver", "Aluminum", "Gold", "Argon"],
        "correct": 2,
    },
    {
        "question": "What is the deepest ocean?",
        "options": ["Atlantic", "Indian", "Arctic", "Pacific"],
        "correct": 3,
    },
]


# ---------------------------------------------------------------------------
# Backend tool — grade an answer and advance state
# ---------------------------------------------------------------------------


@app.tool()
def submit_answer(
    question_index: int,
    selected: int,
    correct: int,
    total_questions: int,
    current_score: int,
) -> dict:
    """Grade an answer and return the updated quiz state.

    Returns a dict with:
    - is_correct: whether the selected answer matched the correct index
    - new_score: the updated cumulative score
    - answered_index: the question that was just answered
    - finished: whether this was the last question
    """
    is_correct = selected == correct
    new_score = current_score + (1 if is_correct else 0)
    finished = (question_index + 1) >= total_questions
    return {
        "is_correct": is_correct,
        "new_score": new_score,
        "answered_index": question_index,
        "finished": finished,
    }


# ---------------------------------------------------------------------------
# UI entry point — the LLM calls this with a topic and generated questions
# ---------------------------------------------------------------------------


@app.ui()
def take_quiz(
    topic: str = "General Knowledge",
    questions: list[dict] | None = None,
) -> PrefabApp:
    """Launch a quiz UI.

    The LLM generates the questions and passes them in:
    - topic: displayed as the heading (e.g. "World Capitals")
    - questions: list of dicts, each with:
        - "question": the question text
        - "options": list of answer strings
        - "correct": index of the correct option

    If no questions are provided, a built-in set is used.
    """
    if questions is None:
        questions = DEFAULT_QUESTIONS
    total = len(questions)
    score = Rx("score")
    current_q = Rx("current_question")
    answered = Rx("answered")

    with Column(gap=6, css_class="p-6 max-w-2xl") as view:
        Heading(f"Quiz: {topic}")

        with Row(gap=3, align="center"):
            Badge(f"{score}/{total} correct", variant="secondary")
            Progress(value=current_q, max=total, size="sm")

        for i, q in enumerate(questions):
            visible = current_q == i
            options = q["options"]
            correct_idx = q["correct"]

            with If(visible):
                with Card():
                    with Column(gap=4, css_class="p-4"):
                        Text(
                            f"Question {i + 1} of {total}",
                            css_class="text-sm font-medium text-muted-foreground",
                        )
                        Heading(q["question"], level=3)

                        with If(~answered):
                            with Column(gap=2):
                                for opt_idx, option in enumerate(options):
                                    on_success_actions = [
                                        SetState("answered", True),
                                        SetState(
                                            "last_correct",
                                            RESULT.is_correct,
                                        ),
                                        SetState("score", RESULT.new_score),
                                    ]
                                    is_last = (i + 1) >= total
                                    if is_last:
                                        on_success_actions.append(
                                            SetState("finished", True),
                                        )

                                    Button(
                                        option,
                                        variant="outline",
                                        css_class="w-full justify-start",
                                        on_click=CallTool(
                                            submit_answer,
                                            arguments={
                                                "question_index": i,
                                                "selected": opt_idx,
                                                "correct": correct_idx,
                                                "total_questions": total,
                                                "current_score": str(score),
                                            },
                                            on_success=on_success_actions,
                                            on_error=ShowToast(
                                                ERROR,
                                                variant="error",
                                            ),
                                        ),
                                    )

                        with If(answered):
                            with Column(gap=2):
                                for opt_idx, option in enumerate(options):
                                    if opt_idx == correct_idx:
                                        Button(
                                            f"{option}",
                                            variant="success",
                                            css_class="w-full justify-start",
                                            disabled=True,
                                        )
                                    else:
                                        Button(
                                            option,
                                            variant="ghost",
                                            css_class="w-full justify-start opacity-50",
                                            disabled=True,
                                        )

                                with If(Rx("last_correct")):
                                    Badge("Correct!", variant="success")
                                with If(~Rx("last_correct")):
                                    Badge(
                                        f"Incorrect — answer: {options[correct_idx]}",
                                        variant="destructive",
                                    )

        with If(answered & ~Rx("finished")):
            Button(
                "Next Question",
                variant="default",
                on_click=[
                    SetState("current_question", current_q + 1),
                    SetState("answered", False),
                    SetState("last_correct", False),
                ],
            )

        with If(Rx("finished") & answered):
            with Card(css_class="border-2 border-primary"):
                with Column(gap=3, css_class="p-4 items-center text-center"):
                    Heading("Quiz Complete!", level=2)
                    Text(
                        f"{score}/{total} correct",
                        css_class="text-2xl font-bold",
                    )
                    Progress(
                        value=score,
                        max=total,
                        variant="success",
                        size="lg",
                    )
                    Muted("Click below to send your results to the conversation.")
                    Button(
                        "Send Results",
                        variant="default",
                        on_click=SendMessage(
                            f'Quiz complete! Topic: "{topic}" '
                            f"— Final score: {score}/{total} correct.",
                        ),
                    )

    initial_state = {
        "score": 0,
        "current_question": 0,
        "answered": False,
        "last_correct": False,
        "finished": False,
    }
    return PrefabApp(view=view, state=initial_state)


mcp = FastMCP("Quiz Server", providers=[app])

if __name__ == "__main__":
    mcp.run(transport="http")
