"""Read-oriented marketplace query facade."""

from app.services.marketplace_service import browse_listings, listing_detail

__all__ = ["browse_listings", "listing_detail"]
