from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, validator
from app.db.database import supabase
from typing import List, Optional
from datetime import datetime, date
import traceback
import sys

router = APIRouter()

class BeneficiaryBase(BaseModel):
    program_id: Optional[str] = None
    first_name: str
    last_name: str
    age: Optional[int] = None
    age_group: Optional[str] = None
    gender: Optional[str] = None
    height: Optional[float] = None  # in cm
    weight: Optional[float] = None  # in kg
    bmi: Optional[float] = None
    weight_status: Optional[str] = None
    address: Optional[str] = None
    contact_number: Optional[str] = None
    registration_date: Optional[str] = None
    dietary_restrictions: Optional[str] = None
    health_conditions: Optional[str] = None

class BeneficiaryCreate(BeneficiaryBase):
    pass

class BeneficiaryUpdate(BeneficiaryBase):
    pass

class BeneficiaryResponse(BeneficiaryBase):
    id: str
    created_at: Optional[str] = None
    program_name: Optional[str] = None

def calculate_bmi(height_cm: float, weight_kg: float) -> float:
    """Calculate BMI from height (cm) and weight (kg)"""
    if not height_cm or not weight_kg or height_cm <= 0 or weight_kg <= 0:
        return None
    height_m = height_cm / 100
    return round(weight_kg / (height_m ** 2), 1)

def get_weight_status(bmi: float) -> str:
    """Determine weight status based on BMI (WHO standards)"""
    if not bmi:
        return None
    if bmi < 18.5:
        return "Underweight"
    elif 18.5 <= bmi < 25:
        return "Normal"
    elif 25 <= bmi < 30:
        return "Overweight"
    else:
        return "Obese"

@router.get("", response_model=List[BeneficiaryResponse])
async def get_beneficiaries():
    try:
        print(f"\n=== GET BENEFICIARIES REQUEST ===", file=sys.stderr)
        
        response = supabase.table("beneficiaries").select(
            "*, programs(name)"
        ).order("created_at", desc=False).execute()
        
        if not response.data:
            return []
        
        beneficiaries = []
        for ben in response.data:
            ben_data = {**ben}
            if ben.get('programs'):
                ben_data['program_name'] = ben['programs'].get('name')
            else:
                ben_data['program_name'] = None
            ben_data.pop('programs', None)
            beneficiaries.append(BeneficiaryResponse(**ben_data))
        
        print(f"Fetched {len(beneficiaries)} beneficiaries", file=sys.stderr)
        return beneficiaries
        
    except Exception as e:
        print(f"Error fetching beneficiaries: {e}", file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error fetching beneficiaries: {str(e)}")

@router.get("/{beneficiary_id}", response_model=BeneficiaryResponse)
async def get_beneficiary(beneficiary_id: str):
    try:
        print(f"\n=== GET BENEFICIARY {beneficiary_id} ===", file=sys.stderr)
        
        response = supabase.table("beneficiaries").select(
            "*, programs(name)"
        ).eq("id", beneficiary_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Beneficiary not found")
        
        ben = response.data[0]
        ben_data = {**ben}
        if ben.get('programs'):
            ben_data['program_name'] = ben['programs'].get('name')
        else:
            ben_data['program_name'] = None
        ben_data.pop('programs', None)
        
        return BeneficiaryResponse(**ben_data)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching beneficiary: {e}", file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error fetching beneficiary: {str(e)}")

@router.post("", response_model=BeneficiaryResponse)
async def create_beneficiary(beneficiary: BeneficiaryCreate):
    try:
        print(f"\n=== CREATE BENEFICIARY REQUEST ===", file=sys.stderr)
        print(f"Received beneficiary data: {beneficiary.dict()}", file=sys.stderr)
        
        if beneficiary.program_id:
            program_check = supabase.table("programs").select("id").eq("id", beneficiary.program_id).execute()
            if not program_check.data:
                raise HTTPException(status_code=400, detail="Invalid program_id: Program does not exist")
        
        # Auto-calculate BMI and weight status
        bmi = calculate_bmi(beneficiary.height, beneficiary.weight)
        weight_status = get_weight_status(bmi)
        
        data = {
            "program_id": beneficiary.program_id,
            "first_name": beneficiary.first_name,
            "last_name": beneficiary.last_name,
            "age": beneficiary.age,
            "age_group": beneficiary.age_group,
            "gender": beneficiary.gender,
            "height": beneficiary.height,
            "weight": beneficiary.weight,
            "bmi": bmi,
            "weight_status": weight_status,
            "address": beneficiary.address,
            "contact_number": beneficiary.contact_number,
            "registration_date": beneficiary.registration_date or date.today().isoformat(),
            "dietary_restrictions": beneficiary.dietary_restrictions,
            "health_conditions": beneficiary.health_conditions,
            "created_at": datetime.now().isoformat()
        }
        
        print(f"Inserting data with BMI {bmi} and status {weight_status}", file=sys.stderr)
        result = supabase.table("beneficiaries").insert(data).execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create beneficiary")
        
        created_id = result.data[0]['id']
        fetch_result = supabase.table("beneficiaries").select(
            "*, programs(name)"
        ).eq("id", created_id).execute()
        
        ben = fetch_result.data[0]
        ben_data = {**ben}
        if ben.get('programs'):
            ben_data['program_name'] = ben['programs'].get('name')
        else:
            ben_data['program_name'] = None
        ben_data.pop('programs', None)
        
        return BeneficiaryResponse(**ben_data)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"\n=== ERROR CREATING BENEFICIARY ===", file=sys.stderr)
        print(f"Error: {str(e)}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Error creating beneficiary: {str(e)}")

@router.put("/{beneficiary_id}", response_model=BeneficiaryResponse)
async def update_beneficiary(beneficiary_id: str, beneficiary: BeneficiaryUpdate):
    try:
        print(f"\n=== UPDATE BENEFICIARY REQUEST ===", file=sys.stderr)
        print(f"Updating beneficiary {beneficiary_id}", file=sys.stderr)
        
        existing = supabase.table("beneficiaries").select("*").eq("id", beneficiary_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Beneficiary not found")
        
        if beneficiary.program_id:
            program_check = supabase.table("programs").select("id").eq("id", beneficiary.program_id).execute()
            if not program_check.data:
                raise HTTPException(status_code=400, detail="Invalid program_id: Program does not exist")
        
        # Auto-calculate BMI and weight status
        bmi = calculate_bmi(beneficiary.height, beneficiary.weight)
        weight_status = get_weight_status(bmi)
        
        data = {
            "program_id": beneficiary.program_id,
            "first_name": beneficiary.first_name,
            "last_name": beneficiary.last_name,
            "age": beneficiary.age,
            "age_group": beneficiary.age_group,
            "gender": beneficiary.gender,
            "height": beneficiary.height,
            "weight": beneficiary.weight,
            "bmi": bmi,
            "weight_status": weight_status,
            "address": beneficiary.address,
            "contact_number": beneficiary.contact_number,
            "registration_date": beneficiary.registration_date,
            "dietary_restrictions": beneficiary.dietary_restrictions,
            "health_conditions": beneficiary.health_conditions
        }
        
        print(f"Update data with BMI {bmi} and status {weight_status}", file=sys.stderr)
        result = supabase.table("beneficiaries").update(data).eq("id", beneficiary_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to update beneficiary")
        
        fetch_result = supabase.table("beneficiaries").select(
            "*, programs(name)"
        ).eq("id", beneficiary_id).execute()
        
        ben = fetch_result.data[0]
        ben_data = {**ben}
        if ben.get('programs'):
            ben_data['program_name'] = ben['programs'].get('name')
        else:
            ben_data['program_name'] = None
        ben_data.pop('programs', None)
        
        return BeneficiaryResponse(**ben_data)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"\n=== ERROR UPDATING BENEFICIARY ===", file=sys.stderr)
        print(f"Error: {str(e)}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Error updating beneficiary: {str(e)}")

@router.delete("/{beneficiary_id}")
async def delete_beneficiary(beneficiary_id: str):
    try:
        print(f"\n=== DELETE BENEFICIARY REQUEST ===", file=sys.stderr)
        
        existing = supabase.table("beneficiaries").select("*").eq("id", beneficiary_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Beneficiary not found")
        
        result = supabase.table("beneficiaries").delete().eq("id", beneficiary_id).execute()
        
        print(f"Delete successful for id: {beneficiary_id}", file=sys.stderr)
        return {"message": "Beneficiary deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"\n=== ERROR DELETING BENEFICIARY ===", file=sys.stderr)
        print(f"Error: {str(e)}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Error deleting beneficiary: {str(e)}")