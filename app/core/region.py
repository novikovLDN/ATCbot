"""
Region awareness for multi-region architecture.

This module provides region detection and awareness for global scale.

IMPORTANT:
- Region awareness is for planning and documentation
- It does NOT affect runtime behavior automatically
- Region failover is manual (operator-controlled)
- No automatic region switching
"""

from enum import Enum
from typing import Optional
import os


class Region(str, Enum):
    """Geographic regions"""
    EU = "eu"  # Europe
    US = "us"  # United States
    ASIA = "asia"  # Asia
    FALLBACK = "fallback"  # Fallback region (default)


class RegionConfig:
    """
    Region configuration and awareness.
    
    Provides region detection and status for multi-region architecture.
    """
    
    def __init__(self):
        """Initialize region configuration"""
        self._current_region: Optional[Region] = None
        self._primary_region: Optional[Region] = None
        self._secondary_regions: list[Region] = []
        self._load_region_config()
    
    def _load_region_config(self) -> None:
        """Load region configuration from environment"""
        # Region from environment variable
        region_str = os.getenv("REGION", "fallback").lower()
        
        try:
            self._current_region = Region(region_str)
        except ValueError:
            self._current_region = Region.FALLBACK
        
        # Primary region (default: current region)
        primary_str = os.getenv("PRIMARY_REGION", region_str).lower()
        try:
            self._primary_region = Region(primary_str)
        except ValueError:
            self._primary_region = self._current_region
        
        # Secondary regions (comma-separated)
        secondary_str = os.getenv("SECONDARY_REGIONS", "").lower()
        if secondary_str:
            for sec_region in secondary_str.split(","):
                sec_region = sec_region.strip()
                try:
                    self._secondary_regions.append(Region(sec_region))
                except ValueError:
                    pass
    
    def current_region(self) -> Region:
        """
        Get current region.
        
        Returns:
            Current region
        """
        return self._current_region or Region.FALLBACK
    
    def is_primary_region(self) -> bool:
        """
        Check if current region is primary.
        
        Returns:
            True if current region is primary, False otherwise
        """
        return self._current_region == self._primary_region
    
    def is_secondary_region(self) -> bool:
        """
        Check if current region is secondary.
        
        Returns:
            True if current region is secondary, False otherwise
        """
        return self._current_region in self._secondary_regions
    
    def primary_region(self) -> Region:
        """
        Get primary region.
        
        Returns:
            Primary region
        """
        return self._primary_region or Region.FALLBACK
    
    def secondary_regions(self) -> list[Region]:
        """
        Get secondary regions.
        
        Returns:
            List of secondary regions
        """
        return list(self._secondary_regions)
    
    def get_region_status(self) -> dict:
        """
        Get region status summary.
        
        Returns:
            Dictionary with region status
        """
        return {
            "current_region": self._current_region.value if self._current_region else "unknown",
            "primary_region": self._primary_region.value if self._primary_region else "unknown",
            "is_primary": self.is_primary_region(),
            "is_secondary": self.is_secondary_region(),
            "secondary_regions": [r.value for r in self._secondary_regions],
        }


# Global singleton instance
_region_config: Optional[RegionConfig] = None


def get_region_config() -> RegionConfig:
    """
    Get or create global region configuration instance.
    
    Returns:
        Global RegionConfig instance
    """
    global _region_config
    
    if _region_config is None:
        _region_config = RegionConfig()
    
    return _region_config


def current_region() -> Region:
    """
    Get current region (convenience function).
    
    Returns:
        Current region
    """
    return get_region_config().current_region()


def is_primary_region() -> bool:
    """
    Check if current region is primary (convenience function).
    
    Returns:
        True if current region is primary, False otherwise
    """
    return get_region_config().is_primary_region()


def is_secondary_region() -> bool:
    """
    Check if current region is secondary (convenience function).
    
    Returns:
        True if current region is secondary, False otherwise
    """
    return get_region_config().is_secondary_region()
