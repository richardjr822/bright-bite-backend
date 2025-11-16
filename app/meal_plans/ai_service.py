import json, uuid, random, os, re
from typing import Dict, Any, List
import hashlib

try:
    import google.generativeai as genai
except ImportError:
    genai = None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-preview-09-2025")
PLAN_DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]

def macro_split(calories: int, style: str = "balanced") -> tuple[int,int,int]:
    if style == "low-carb":
        p_pct,c_pct,f_pct = 0.4,0.2,0.4
    elif style == "high-protein":
        p_pct,c_pct,f_pct = 0.5,0.3,0.2
    else:
        p_pct,c_pct,f_pct = 0.3,0.4,0.3
    p = int(calories * p_pct / 4)
    c = int(calories * c_pct / 4)
    f = int(calories * f_pct / 9)
    return p,c,f

def even_calorie_split(total: int, meals: int) -> List[int]:
    if meals <= 0: return []
    base = total // meals
    rem = total % meals
    arr = [base]*meals
    for i in range(rem): arr[i]+=1
    return arr

def preference_signature(prefs: Dict[str, Any]) -> str:
    keys = [
        "goal","macroPreference","calorieTarget","mealsPerDay",
        "dietaryPreference","avoidFoods","allergies","healthConditions",
        "specialGoals","dailyBudget","cookingTime","cookingMethod",
        "mealComplexity","mealPrepStyle","appetite"
    ]
    acc = []
    for k in keys:
        v = prefs.get(k)
        if isinstance(v, list):
            acc.append(f"{k}=" + ",".join(sorted(map(str,v))))
        else:
            acc.append(f"{k}={v}")
    raw = "|".join(acc)
    return hashlib.sha256(raw.encode()).hexdigest()

def _prompt(prefs: Dict[str, Any], split: List[int]) -> str:
    return f"""
Return ONLY strict JSON.

Days: monday..sunday -> array[Meal]
Meal fields:
  name (Filipino or accessible dish)
  type (Breakfast|Lunch|Dinner|Snack)
  calories (int)
  prep_time (int minutes)
  description (<=200 chars; MUST explain why this meal matches: goal={prefs.get('goal')}, macro={prefs.get('macroPreference')}, appetite={prefs.get('appetite','average')}, budget={prefs.get('dailyBudget','n/a')} PHP, cooking methods {', '.join(prefs.get('cookingMethod', [])) or 'flexible'}, special goals {', '.join(prefs.get('specialGoals', [])) or 'none'}, health conditions {', '.join(prefs.get('healthConditions', [])) or 'none'}; mention if higher protein for muscle/fat-loss or higher calories for gain; avoid listing ingredients.)
  macros {{protein, carbs, fats}} (int grams)

Targets:
  daily_calories ≈ {int(prefs.get('calorieTarget',2000))}
  meals_per_day = {int(prefs.get('mealsPerDay',3))}
  meal_calorie_split ≈ {split}

Constraints:
- Reflect dietary preferences: {', '.join(prefs.get('dietaryPreference', [])) or 'none'}
- Exclude allergies: {', '.join(prefs.get('allergies', [])) or 'none'}
- Exclude avoid foods: {prefs.get('avoidFoods','none')}
- Adapt complexity: {prefs.get('mealComplexity','simple')} and cooking time ≤ {prefs.get('cookingTime','flex')}
- Use allowed cooking methods when possible: {', '.join(prefs.get('cookingMethod', [])) or 'any'}
- Portion sizing: appetite={prefs.get('appetite','average')} (light smaller calories, heavy slightly larger within total)

Rules:
- No ingredients list
- No instructions
- No extra keys
- Diverse dishes (avoid repeating a dish >2/week)
- description must be meaningful, concise, user-focused (no marketing fluff)

Output ONLY the JSON object.
""".strip()

def _extract_json(text: str) -> str:
    m = re.search(r"```json\s*({.*})\s*```", text, re.DOTALL|re.IGNORECASE)
    if m: return m.group(1)
    start = text.find('{'); end = text.rfind('}')+1
    return text[start:end] if start!=-1 and end>start else text

def _clean(data: Dict[str, Any]) -> Dict[str, Any]:
    for day in PLAN_DAYS:
        if day not in data: data[day] = []
        meals = data[day]
        if not isinstance(meals, list): data[day] = []; continue
        for meal in meals:
            if not isinstance(meal, dict): continue
            meal['id'] = meal.get('id') or str(uuid.uuid4())
            meal['calories'] = int(meal.get('calories', 0))
            meal['prep_time'] = int(meal.get('prep_time', meal.get('prepTime', 15)))
            macros = meal.get('macros', {})
            if not isinstance(macros, dict): macros = {}
            macros = {
                'protein': int(macros.get('protein', 0)),
                'carbs': int(macros.get('carbs', 0)),
                'fats': int(macros.get('fats', 0)),
            }
            if macros['protein']==0 and macros['carbs']==0 and macros['fats']==0:
                p,c,f = macro_split(meal['calories'], (meal.get('style') or data.get('macroPreference') or 'balanced'))
                macros = {'protein': p,'carbs': c,'fats': f}
            meal['macros'] = macros
            # Remove any unexpected keys
            for k in list(meal.keys()):
                if k not in ['id','name','type','meal_type','calories','prep_time','description','macros']:
                    del meal[k]
    return data

def _rule_based(prefs: Dict[str, Any]) -> Dict[str, Any]:
    total = int(prefs.get("calorieTarget",2000) or 2000)
    meals_n = int(prefs.get("mealsPerDay",3) or 3)
    style = prefs.get("macroPreference","balanced")
    goal = prefs.get("goal","maintain")
    split = even_calorie_split(total, meals_n)
    plan = {}
    for day in PLAN_DAYS:
        day_meals = []
        for i,kcal in enumerate(split):
            meal_type = ["Breakfast","Lunch","Dinner","Snack"][min(i,3)]
            p,c,f = macro_split(kcal, style)
            desc = f"Filipino-inspired {meal_type.lower()} for {goal}; ~{kcal} kcal P{p}g/C{c}g/F{f}g."
            day_meals.append({
                "id": str(uuid.uuid4()),
                "name": f"{goal.title()} {meal_type}",
                "type": meal_type,
                "calories": kcal,
                "prep_time": random.choice([10,15,20,25,30]),
                "description": desc,
                "macros": {"protein": p,"carbs": c,"fats": f}
            })
        plan[day] = day_meals
    return plan

def ai_generate(preferences: Dict[str, Any]) -> Dict[str, Any]:
    try:
        total = int(preferences.get("calorieTarget") or 2000)
        meals_n = int(preferences.get("mealsPerDay") or 3)
        if total<=0: total=2000
        if meals_n<=0: meals_n=3
    except:
        total=2000; meals_n=3
    split = even_calorie_split(total, meals_n)
    prompt = _prompt(preferences, split)

    if not GEMINI_API_KEY or genai is None:
        return _rule_based(preferences)

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        raw = (response.text or "").strip()
        json_str = _extract_json(raw)
        data = json.loads(json_str)
        return _clean(data)
    except Exception as e:
        print(f"[Gemini fallback] {e}")
        return _rule_based(preferences)