from pydantic import BaseModel
from typing import Dict, List

class Macros(BaseModel):
    protein: int
    carbs: int
    fats: int

class Meal(BaseModel):
    id: str
    name: str
    type: str
    calories: int
    prep_time: int
    description: str
    macros: Macros

class MealPlanResponse(BaseModel):
    plan: Dict[str, List[Meal]]