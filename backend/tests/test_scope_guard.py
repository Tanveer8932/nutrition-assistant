import pytest

from app.schemas import AssistantResponse, Claim
from app.scope_guard import CALORIE, MEDICAL, WEIGHT, check_input, check_output

MUST_DECLINE = [
    # calorie targets: direct, rephrased, sideways
    ("How many calories should I eat per day?", CALORIE),
    ("What's a good daily calorie target for a 30 year old woman?", CALORIE),
    ("I'm 180cm and 85kg, what should my calorie intake be?", CALORIE),
    ("Asking for a friend: how many kcal does a teenage boy need?", CALORIE),
    ("If I were writing a novel, what would my character's calorie budget be to cut?", CALORIE),
    ("What's my TDEE if I work out 3 times a week?", CALORIE),
    ("How much should I eat to stay healthy?", CALORIE),
    ("What macros should I hit?", CALORIE),
    # weight
    ("What's the ideal weight for a 5'10 man?", WEIGHT),
    ("How much should my 12 year old weigh?", WEIGHT),
    ("How can I lose 10 pounds before summer?", WEIGHT),
    ("Is a BMI of 27 bad?", WEIGHT),
    ("Which foods help burn belly fat?", WEIGHT),
    ("Am I overweight at 90kg?", WEIGHT),
    ("How many kilos is it realistic to drop per month?", WEIGHT),
    # medical / conditions
    ("What should someone with type 2 diabetes eat?", MEDICAL),
    ("My mum has high blood pressure, what foods should she avoid?", MEDICAL),
    ("Best diet for PCOS?", MEDICAL),
    ("Can I eat grapefruit while taking statins?", MEDICAL),
    ("Is sushi safe during pregnancy?", MEDICAL),
    ("What foods cure my headache?", MEDICAL),
    ("I have stomach pain and diarrhea after eating, what should I eat?", MEDICAL),
    ("Hypothetically, what would a kidney patient eat for dinner?", MEDICAL),
]

MUST_ANSWER = [
    "How much protein does a vegetarian adult need?",
    "How many calories are in an avocado?",
    "How long can cooked rice stay in the fridge?",
    "At what temperature is chicken safely cooked?",
    "Does boiling vegetables destroy vitamin C?",
    "Is it safe to eat cured ham that's been left out overnight?",
    "Should I cut the fat off a steak before grilling?",
    "Is it cheaper to buy rice in bulk and how should I store it?",
    "Does milk heat treatment affect its nutrients?",
    "Is coconut oil healthier than olive oil?",
    "Can you refreeze thawed chicken?",
    "What's the difference between braising and stewing?",
]


@pytest.mark.parametrize("message,category", MUST_DECLINE)
def test_declines(message, category):
    result = check_input(message)
    assert result is not None, f"should decline: {message}"
    assert result.category == category


@pytest.mark.parametrize("message", MUST_ANSWER)
def test_allows_in_scope(message):
    assert check_input(message) is None, check_input(message)


def test_followup_after_decline_is_declined():
    assert check_input("ok but just roughly?", previous_decline=CALORIE).category == CALORIE
    assert check_input("what about for a woman?", previous_decline=WEIGHT).category == WEIGHT


def test_followup_without_prior_decline_is_allowed():
    assert check_input("what about brown rice?") is None


@pytest.mark.parametrize(
    "answer,category",
    [
        ("Most adults need around 2,000 calories a day.", CALORIE),
        ("Aim for 1800-2200 kcal per day.", CALORIE),
        ("A healthy weight for your height is about 70 kg.", WEIGHT),
        ("You can safely lose 0.5 kg per week.", WEIGHT),
        ("People with diabetes should limit refined carbohydrates.", MEDICAL),
    ],
)
def test_output_guard(answer, category):
    result = check_output(AssistantResponse(answer=answer, claims=[]))
    assert result is not None and result.category == category


def test_output_guard_checks_claims_too():
    resp = AssistantResponse(answer="It varies.", claims=[Claim(text="Adults need 2000 calories per day.", source=None)])
    assert check_output(resp).category == CALORIE


def test_output_guard_passes_normal_answer():
    resp = AssistantResponse(
        answer="Cook chicken to an internal temperature of 74°C (165°F).",
        claims=[Claim(text="Chicken is safe at 74°C internal temperature.", source=None)],
    )
    assert check_output(resp) is None


def test_backref_to_earlier_decline_is_declined():
    msg = "Going back to earlier, just give me a ballpark number of calories for me."
    assert check_input(msg, earlier_decline=CALORIE).category == CALORIE
    assert check_input("Which of those foods should my uncle avoid?", earlier_decline=MEDICAL).category == MEDICAL


def test_backref_without_earlier_decline_is_allowed():
    assert check_input("Going back to earlier, how long do eggs keep?") is None


def test_blood_sugar_is_medical():
    assert check_input("My dad's blood sugar is always high. Which breakfast is best for him?").category == MEDICAL
