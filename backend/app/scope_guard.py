"""Scope limits enforced in code, independent of the system prompt.

Two stages:
  1. check_input  - runs before the model is called. A match means the model
                    is never called and a fixed decline is returned.
  2. check_output - runs on the parsed model response. Catches out-of-scope
                    content that slipped past stage 1 (sideways phrasing the
                    model chose to answer anyway).

Deliberately conservative: a false decline costs a rephrase, a false answer
can cost someone's health. Both stages are deterministic so the same input
always gets the same decision.
"""
import re
from dataclasses import dataclass
from typing import Optional

from .schemas import AssistantResponse

CALORIE = "calorie_target"
WEIGHT = "weight_recommendation"
MEDICAL = "medical_advice"


@dataclass(frozen=True)
class GuardResult:
    category: str
    stage: str  # "input" | "output"
    pattern: str


def _rx(*parts: str) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in parts]


_PERSON = r"(i|me|my|myself|he|she|they|him|her|them|someone|somebody|a person|people|an? (adult|man|woman|child|kid|teen|athlete)|my \w+)"

INPUT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    CALORIE: _rx(
        # calories/kcal + a target-ish word, in either order
        r"\b(calorie|calories|kcal|cals?)\b.{0,60}\b(should|need|needs|target|goal|budget|limit|allowance|intake|deficit|surplus|maintenance|maintain|a day|per day|each day|daily|a week|per week|eat to|to (lose|gain|cut|bulk))\b",
        r"\b(should|need|needs|target|goal|budget|limit|allowance|deficit|surplus|maintenance|daily)\b.{0,40}\b(calorie|calories|kcal|cals)\b",
        r"\b(calorie|calories|kcal)\b.{0,30}\bfor (me|him|her|them|us|myself|my \w+|someone|somebody|a (man|woman|person|child|kid|teen))\b",
        r"\b(tdee|bmr|basal metabolic|maintenance (level|intake))\b",
        r"\bmacros?\b.{0,40}\b(should|need|target|goal|split|for " + _PERSON + r")\b",
        r"\bhow much (food )?should " + _PERSON + r" eat\b",
        r"\b(energy|food) (needs|requirements?|intake) (for|of) " + _PERSON + r"\b",
    ),
    WEIGHT: _rx(
        r"\b(ideal|healthy|target|goal|right|normal|perfect|best)\s+(body\s*)?weight\b",
        r"\bshould\b.{0,50}\bweigh\b",
        r"\bhow (heavy|light|thin|skinny)\b.{0,30}\bshould\b",
        r"\b(lose|losing|lost|drop|shed|gain|gaining|put on)\b.{0,30}\b(weight|pounds?|lbs?|kg|kgs|kilos?|stone|body ?fat|belly fat)\b",
        r"\b(pounds?|lbs?|kg|kgs|kilos?|stone)\b.{0,20}\b(lose|losing|drop|dropping|shed|gain|put on)\b",
        r"\b(burn|burning|melt)\b.{0,15}\b(body |belly )?fat\b",
        r"\b(weight|fat)[- ]?(loss|gain)\b",
        r"\bbmi\b|\bbody mass index\b",
        r"\b(slim down|get lean|get skinny|bulking|bulk up|cutting (phase|diet)|lean out)\b",
        r"\b(am i|is (he|she|my \w+)|are they)\b.{0,20}\b(over ?weight|under ?weight|obese|too (fat|thin|skinny|heavy))\b",
    ),
    MEDICAL: _rx(
        # named conditions (not food-borne pathogens: those are food-safety topics)
        r"\b(pre-?diabet\w*|diabet\w*|type [12]|insulin resistan\w*|blood sugar|blood glucose|hypertension|high blood pressure|low blood pressure|cholesterol|heart (disease|failure|condition)|cardiac|stroke|kidney|renal|dialysis|liver disease|fatty liver|cirrhosis|cancer|chemo\w*|tumou?r|ibs|irritable bowel|crohn'?s|colitis|celiac|coeliac|gout|pcos|polycystic|thyroid|hashimoto|anae?mi\w*|osteoporosis|arthritis|gerd|acid reflux|heartburn|ulcer|gallstones?|gallbladder|diverticul\w*|epilep\w*|dementia|alzheimer\w*|migraines?|asthma|eczema|psoriasis|acne|hiv|lupus|multiple sclerosis|autism|adhd|depress\w*|anxiety|anorexi\w*|bulimi\w*|binge[- ]eating|eating disorder|obes\w*|pregnan\w*|breast-?feeding|lactating|menopaus\w*|allergic|allergy|allergies|intoleran\w*|autoimmune|inflammation|high triglycerides)\b",
        # medication / clinical treatment
        r"\b(medications?|meds|prescri\w+|warfarin|metformin|statins?|insulin|blood thinners?|antidepressants?|ssri|maoi|levothyroxine|ozempic|semaglutide|wegovy|mounjaro|drug interactions?)\b",
        # treatment intent
        # ("cure"/"treatment" alone would catch cured meats and heat treatment)
        r"\b(cure|cures|heal|heals|remedy|remedies|treat|treats|get rid of|fight off)\b.{0,30}\b(my|his|her|their|symptoms?|illness|disease|condition|infection|cold|flu|cough|headache|pain|inflammation)\b",
        r"\b(diagnos\w*|reverse my|flare[- ]?ups?)\b",
        # personal symptoms
        r"\b(i have|i've got|i am having|i'm having|i keep getting|my \w+ has)\b.{0,40}\b(pain|ache|diarrh\w*|vomit\w*|nause\w*|constipat\w*|bloat\w*|rash|fever|cramps?|dizz\w*|fatigue|symptoms?)\b",
    ),
}

# Short follow-ups that only make sense as a continuation of a declined request
# ("ok but roughly?", "just a ballpark", "what about for a woman?").
_FOLLOWUP = re.compile(
    r"^\s*(ok(ay)?|fine|sure|but|and|so|just|what about|how about|roughly|ballpark|approximately|a rough|an estimate|guess|hypothetically|come on|please)\b",
    re.IGNORECASE,
)

# Explicit references back to an earlier turn ("going back to earlier...",
# "which of those foods..."). If any earlier request in the conversation was
# declined, these re-raise it and are declined too.
_BACKREF = re.compile(
    r"\b(back to|going back|earlier|before|you (said|mentioned)|i asked|my (first|earlier|previous) question|of those|those foods|that list|same question|again)\b",
    re.IGNORECASE,
)

OUTPUT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    CALORIE: _rx(
        r"\b\d[\d,]*\s*(?:(?:-|–|to)\s*\d[\d,]*\s*)?(k?cal|kilocalories|calories)\s*(a|per|each)\s*(day|week)\b",
        r"\b(calorie|caloric) (target|goal|budget|deficit|surplus)\b.{0,40}\d",
        r"\b(eat|consume|aim for|target)\b.{0,20}\b\d[\d,]{2,}\s*(k?cal|calories)\b",
    ),
    WEIGHT: _rx(
        r"\b(should|ideal|healthy|target|goal)\b.{0,30}\bweigh(t)?\b.{0,30}\d",
        r"\bbmi\b.{0,30}\d",
        r"\b(lose|gain)\b.{0,20}\d+(\.\d+)?\s*(kg|lbs?|pounds?|kilos?)\s*(a|per|each)\s*(week|month)\b",
    ),
    MEDICAL: _rx(
        r"\b(if you have|people with|those with|for someone with|patients with|in patients)\b.{0,40}\b(diabet\w*|hypertension|high blood pressure|kidney|heart disease|cancer|ibs|crohn'?s|colitis|celiac|coeliac|gout|pcos|thyroid|gerd|reflux|anemi\w*|anaemi\w*)\b",
        r"\b(dose|dosage|mg of|milligrams of)\b.{0,40}\b(medication|insulin|metformin|warfarin|statin)\b",
    ),
}


def _match(patterns: dict[str, list[re.Pattern[str]]], text: str) -> Optional[tuple[str, str]]:
    for category, rxs in patterns.items():
        for rx in rxs:
            if rx.search(text):
                return category, rx.pattern
    return None


def check_input(
    message: str,
    previous_decline: Optional[str] = None,
    earlier_decline: Optional[str] = None,
) -> Optional[GuardResult]:
    """Return a GuardResult if the user message must be declined.

    `previous_decline`: category if the immediately preceding assistant turn
    was a decline; short follow-ups to it are declined too.
    `earlier_decline`: category of the most recent decline anywhere in the
    conversation; messages that explicitly refer back to it are declined.
    """
    hit = _match(INPUT_PATTERNS, message)
    if hit:
        return GuardResult(category=hit[0], stage="input", pattern=hit[1])
    if previous_decline and len(message) < 120 and _FOLLOWUP.search(message):
        return GuardResult(category=previous_decline, stage="input", pattern="followup-to-decline")
    if earlier_decline and _BACKREF.search(message):
        return GuardResult(category=earlier_decline, stage="input", pattern="backref-to-decline")
    return None


def check_output(resp: AssistantResponse) -> Optional[GuardResult]:
    """Return a GuardResult if the model's answer contains out-of-scope content."""
    text = resp.answer + "\n" + "\n".join(c.text for c in resp.claims)
    hit = _match(OUTPUT_PATTERNS, text)
    if hit:
        return GuardResult(category=hit[0], stage="output", pattern=hit[1])
    return None
