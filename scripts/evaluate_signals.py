"""Evaluation harness: checks that ai_score/confidence move in the expected
direction on texts with a known likely origin. Not a rigorous benchmark —
a sanity check that the combined signal is directionally meaningful before
trusting it to end users. Run with: python -m scripts.evaluate_signals
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from detection.scoring import classify

SAMPLES = [
    (
        "human_moby_dick",
        "human",
        "Call me Ishmael. Some years ago—never mind how long precisely—having "
        "little or no money in my purse, and nothing particular to interest "
        "me on shore, I thought I would sail about a little and see the "
        "watery part of the world. It is a way I have of driving off the "
        "spleen, and regulating the circulation. Whenever I find myself "
        "growing grim about the mouth; whenever it is a damp, drizzly "
        "November in my soul; whenever I find myself involuntarily pausing "
        "before coffin warehouses, and bringing up the rear of every "
        "funeral I meet, and especially whenever my hypos get such an "
        "upper hand of me, that it requires a strong moral principle to "
        "prevent me from deliberately stepping into the street, and "
        "methodically knocking people's hats off—then, I account it high "
        "time to get to sea as soon as I can.",
    ),
    (
        "human_diary",
        "human",
        "ok so today was a mess. i overslept, missed the bus, ran three "
        "blocks in the rain and still got to work twelve minutes late, and "
        "of course that's the exact day my manager decides to actually show "
        "up on time. spent the rest of the morning pretending my coffee "
        "wasn't cold. anyway. small win: the vending machine finally "
        "restocked the good pretzels.",
    ),
    (
        "ai_product_copy",
        "ai",
        "Our wireless earbuds offer a seamless listening experience, "
        "combining advanced technology with sleek design. With up to 5 "
        "hours of playback time, you can enjoy your favorite music or "
        "podcasts on the go. The earbuds are designed to fit comfortably "
        "in your ears, providing a secure and stable fit. Additionally, "
        "they feature intuitive controls, allowing you to easily manage "
        "your music and take hands-free calls. The earbuds are also sweat "
        "and water resistant, making them perfect for exercising or "
        "everyday use. Furthermore, they come with a compact charging "
        "case, providing an extra 10 hours of battery life. Overall, our "
        "wireless earbuds are a great choice for anyone looking for a "
        "convenient and high-quality listening solution.",
    ),
    (
        "ai_motivational",
        "ai",
        "Perseverance is the key to unlocking your full potential. When "
        "faced with obstacles and setbacks, it's easy to give up and lose "
        "sight of your goals. However, it's in these moments that "
        "perseverance shines through. By pushing forward, even when the "
        "journey gets tough, you build resilience and character. Every "
        "step forward, no matter how small, brings you closer to achieving "
        "your dreams. Don't let fear or doubt hold you back - keep moving "
        "forward, stay focused, and trust that your hard work and "
        "determination will ultimately lead to success.",
    ),
    (
        "borderline_formal_human",
        "human (formal/technical style)",
        "The bridge inspection identified three areas of concern. The "
        "north pier showed minor surface corrosion consistent with "
        "expected wear. The expansion joints were functioning within "
        "tolerance. The deck coating requires reapplication within the "
        "next eighteen months. No structural deficiencies were observed "
        "during this cycle.",
    ),
    (
        "borderline_edited_ai",
        "ai (lightly human-edited)",
        "I've been thinking a lot about remote work lately. There are "
        "genuine tradeoffs — flexibility and no commute on one side, "
        "isolation and blurred work-life boundaries on the other. Studies "
        "show productivity varies widely by individual and role type.",
    ),
]


def main():
    print(
        f"{'sample':<26}{'expected':<28}{'llm':>6}{'stylo':>7}"
        f"{'ai_score':>10}{'confidence':>12}  verdict"
    )
    print("-" * 100)
    for name, expected, text in SAMPLES:
        result = classify(text)
        print(
            f"{name:<26}{expected:<28}"
            f"{result['signals']['llm']['score']:>6.2f}"
            f"{result['signals']['stylometric']['score']:>7.2f}"
            f"{result['ai_score']:>10.3f}"
            f"{result['confidence']:>12.3f}  {result['verdict']}"
        )


if __name__ == "__main__":
    main()
