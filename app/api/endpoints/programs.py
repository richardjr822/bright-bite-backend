from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from app.db.database import supabase
from datetime import datetime, date
import traceback
import sys

router = APIRouter()

class ProgramBase(BaseModel):
    name: str
    description: Optional[str] = ""
    location: str
    event_date: str
    event_time: Optional[str] = None
    status: str  # Upcoming, Ongoing, Completed, Cancelled
    max_participants: Optional[int] = None
    contact_person: Optional[str] = None
    contact_number: Optional[str] = None

class ProgramCreate(ProgramBase):
    pass

class ProgramUpdate(ProgramBase):
    pass

class ProgramResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    location: str
    event_date: str
    event_time: Optional[str] = None
    status: str
    max_participants: Optional[int] = None
    contact_person: Optional[str] = None
    contact_number: Optional[str] = None
    beneficiaries_count: int = 0
    days_until_event: Optional[int] = None
    is_past_event: bool = False
    created_at: Optional[str] = None

def calculate_days_until_event(event_date_str, status):
    """Calculate days until the event"""
    if not event_date_str or status in ["Completed", "Cancelled"]:
        return None
    
    try:
        event_date = datetime.fromisoformat(event_date_str.replace('Z', '+00:00')).date() if 'T' in event_date_str else date.fromisoformat(event_date_str)
        today = date.today()
        days_until = (event_date - today).days
        
        # If event is today
        if days_until == 0:
            return 0
        # If event is in the future
        elif days_until > 0:
            return days_until
        # If event is in the past
        else:
            return None
    except Exception as e:
        print(f"Error calculating days until event: {e}", file=sys.stderr)
        return None

def is_past_event(event_date_str):
    """Check if the event date has passed"""
    if not event_date_str:
        return False
    
    try:
        event_date = datetime.fromisoformat(event_date_str.replace('Z', '+00:00')).date() if 'T' in event_date_str else date.fromisoformat(event_date_str)
        today = date.today()
        return today > event_date
    except Exception as e:
        print(f"Error checking if past event: {e}", file=sys.stderr)
        return False

def count_beneficiaries(program_id):
    """Count beneficiaries enrolled in this program by program_id (UUID)"""
    try:
        if not program_id:
            return 0
        
        print(f"Counting beneficiaries for program_id: {program_id}", file=sys.stderr)
        response = supabase.table("beneficiaries").select("id", count="exact").eq("program_id", program_id).execute()
        count = response.count if hasattr(response, 'count') else len(response.data) if response.data else 0
        print(f"Found {count} beneficiaries for program_id: {program_id}", file=sys.stderr)
        return count
    except Exception as e:
        print(f"Error counting beneficiaries for program_id {program_id}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 0

def enrich_program_data(program):
    """Add calculated fields to program data"""
    days_until = calculate_days_until_event(program.get('event_date'), program.get('status'))
    past_event = is_past_event(program.get('event_date'))
    beneficiaries_count = count_beneficiaries(program.get('id'))
    
    return {
        **program,
        'days_until_event': days_until,
        'is_past_event': past_event,
        'beneficiaries_count': beneficiaries_count
    }

@router.get("", response_model=List[ProgramResponse])
async def get_programs():
    try:
        print(f"\n=== GET PROGRAMS REQUEST ===", file=sys.stderr)
        response = supabase.table("programs").select("*").order("event_date", desc=False).execute()
        print(f"Fetched {len(response.data) if response.data else 0} programs", file=sys.stderr)
        
        if not response.data:
            return []
        
        # Enrich each program with calculated fields
        enriched_programs = [enrich_program_data(program) for program in response.data]
        return [ProgramResponse(**program) for program in enriched_programs]
    except Exception as e:
        print(f"Error fetching programs: {e}", file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error fetching programs: {str(e)}")

@router.get("/{program_id}", response_model=ProgramResponse)
async def get_program(program_id: str):
    try:
        print(f"\n=== GET PROGRAM REQUEST ===", file=sys.stderr)
        print(f"Fetching program with id: {program_id}", file=sys.stderr)
        
        response = supabase.table("programs").select("*").eq("id", program_id).execute()
        
        if not response.data:
            print(f"Program not found: {program_id}", file=sys.stderr)
            raise HTTPException(status_code=404, detail="Program not found")
        
        print(f"Found program: {response.data[0]['name']}", file=sys.stderr)
        
        # Enrich response with calculated fields
        enriched_data = enrich_program_data(response.data[0])
        return ProgramResponse(**enriched_data)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching program: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Error fetching program: {str(e)}")

@router.post("", response_model=ProgramResponse)
async def create_program(program: ProgramCreate):
    try:
        print(f"\n=== CREATE PROGRAM REQUEST ===", file=sys.stderr)
        print(f"Received program data: {program.dict()}", file=sys.stderr)
        
        # Validate event date
        try:
            event_date = datetime.fromisoformat(program.event_date.replace('Z', '+00:00')).date() if 'T' in program.event_date else date.fromisoformat(program.event_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid event date format. Use YYYY-MM-DD")
        
        # Prepare data for insertion
        data = {
            "name": program.name,
            "description": program.description or "",
            "location": program.location,
            "event_date": program.event_date,
            "event_time": program.event_time,
            "status": program.status,
            "max_participants": program.max_participants,
            "contact_person": program.contact_person,
            "contact_number": program.contact_number,
            "created_at": datetime.now().isoformat()
        }
        
        print(f"Prepared data for insertion: {data}", file=sys.stderr)
        
        # Insert into database
        result = supabase.table("programs").insert(data).execute()
        print(f"Insert result data: {result.data}", file=sys.stderr)
        
        if not result.data:
            print(f"No data returned from insert", file=sys.stderr)
            raise HTTPException(status_code=500, detail="Failed to create program - no data returned")
        
        print(f"Successfully created program with id: {result.data[0]['id']}", file=sys.stderr)
        
        # Enrich response with calculated fields
        enriched_data = enrich_program_data(result.data[0])
        return ProgramResponse(**enriched_data)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"\n=== ERROR CREATING PROGRAM ===", file=sys.stderr)
        print(f"Error type: {type(e).__name__}", file=sys.stderr)
        print(f"Error message: {str(e)}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Error creating program: {str(e)}")

@router.put("/{program_id}", response_model=ProgramResponse)
async def update_program(program_id: str, program: ProgramUpdate):
    try:
        print(f"\n=== UPDATE PROGRAM REQUEST ===", file=sys.stderr)
        print(f"Updating program {program_id}", file=sys.stderr)
        print(f"Received data: {program.dict()}", file=sys.stderr)
        
        # Check if program exists
        existing = supabase.table("programs").select("*").eq("id", program_id).execute()
        if not existing.data:
            print(f"Program not found: {program_id}", file=sys.stderr)
            raise HTTPException(status_code=404, detail="Program not found")
        
        print(f"Found existing program: {existing.data[0]['name']}", file=sys.stderr)
        
        # Validate event date
        try:
            event_date = datetime.fromisoformat(program.event_date.replace('Z', '+00:00')).date() if 'T' in program.event_date else date.fromisoformat(program.event_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid event date format. Use YYYY-MM-DD")
        
        # Prepare update data
        data = {
            "name": program.name,
            "description": program.description or "",
            "location": program.location,
            "event_date": program.event_date,
            "event_time": program.event_time,
            "status": program.status,
            "max_participants": program.max_participants,
            "contact_person": program.contact_person,
            "contact_number": program.contact_number
        }
        
        print(f"Update data: {data}", file=sys.stderr)
        
        # Update the program
        result = supabase.table("programs").update(data).eq("id", program_id).execute()
        
        if not result.data:
            print(f"No data returned from update", file=sys.stderr)
            raise HTTPException(status_code=500, detail="Failed to update program")
        
        print(f"Update successful for id: {program_id}", file=sys.stderr)
        
        # Enrich response with calculated fields
        enriched_data = enrich_program_data(result.data[0])
        return ProgramResponse(**enriched_data)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"\n=== ERROR UPDATING PROGRAM ===", file=sys.stderr)
        print(f"Error type: {type(e).__name__}", file=sys.stderr)
        print(f"Error message: {str(e)}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Error updating program: {str(e)}")

@router.delete("/{program_id}")
async def delete_program(program_id: str):
    try:
        print(f"\n=== DELETE PROGRAM REQUEST ===", file=sys.stderr)
        print(f"Attempting to delete program with id: {program_id}", file=sys.stderr)
        
        # Check if program exists
        existing = supabase.table("programs").select("*").eq("id", program_id).execute()
        if not existing.data:
            print(f"Program not found: {program_id}", file=sys.stderr)
            raise HTTPException(status_code=404, detail="Program not found")
        
        print(f"Found program to delete: {existing.data[0]['name']}", file=sys.stderr)
        
        # Check if there are beneficiaries enrolled
        beneficiaries_count = count_beneficiaries(program_id)
        if beneficiaries_count > 0:
            print(f"Cannot delete program with {beneficiaries_count} enrolled beneficiaries", file=sys.stderr)
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot delete program. There are {beneficiaries_count} beneficiaries enrolled. Please remove them first."
            )
        
        # Delete the program
        result = supabase.table("programs").delete().eq("id", program_id).execute()
        
        print(f"Delete successful for id: {program_id}", file=sys.stderr)
        return {"message": "Program deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"\n=== ERROR DELETING PROGRAM ===", file=sys.stderr)
        print(f"Error type: {type(e).__name__}", file=sys.stderr)
        print(f"Error message: {str(e)}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Error deleting program: {str(e)}")