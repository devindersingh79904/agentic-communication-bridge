from fastapi import APIRouter
from app.core.logger import get_logger
from app.schemas.base_response import BaseSuccessResponse
from app.schemas.metadata_schema import EnumMetadataResponse
from app.services.metadata_service import get_all_enums_metadata
from app.utils.response_builder import success_response

router = APIRouter(prefix="/v1/metadata", tags=["Metadata"])
logger = get_logger("api.metadata")

@router.get("/enums", response_model=BaseSuccessResponse)
async def get_enum_metadata():
    """
    Exposes all backend enums dynamically to avoid hardcoded frontend constants.
    """
    logger.info("Enum metadata request received")
    
    # Fetch all enum metadata from the service layer
    metadata = get_all_enums_metadata()
    
    # Wrap in centralized success response format
    response = success_response(
        message="Enum metadata fetched successfully",
        data=metadata.model_dump()
    )
    
    logger.info("Enum metadata served successfully")
    return response
