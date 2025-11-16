import math
def macro_split(calories: int, style: str):
    # returns grams (protein, carbs, fats)
    styles = {
        "balanced": (0.30, 0.45, 0.25),
        "high-protein": (0.40, 0.35, 0.25),
        "low-carb": (0.35, 0.30, 0.35),
    }
    p, c, f = styles.get(style, styles["balanced"])
    prot_g = round((calories * p) / 4)
    carb_g = round((calories * c) / 4)
    fat_g  = round((calories * f) / 9)
    return prot_g, carb_g, fat_g

def even_calorie_split(total: int, meals: int):
    base = total // meals
    arr = [base] * max(meals, 1)
    diff = total - base * max(meals, 1)
    for i in range(max(diff, 0)):
        arr[i % len(arr)] += 1
    return arr
