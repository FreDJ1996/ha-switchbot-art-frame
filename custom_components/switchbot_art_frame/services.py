"""Actions for SwitchBot Art Frame Tools."""

import probatio as vol

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv, service

from .const import (
    ATTR_IMAGE,
    ATTR_IMAGE_DATA,
    ATTR_IMAGE_ID,
    ATTR_IMAGE_PATH,
    ATTR_IMAGE_URL,
    ATTR_MAKE_ROOM,
    ATTR_SHOW,
    DOMAIN,
    IMAGE_KEYWORDS,
    SERVICE_DELETE_IMAGE,
    SERVICE_DOWNLOAD_IMAGE,
    SERVICE_SHOW_IMAGE,
    SERVICE_UPLOAD_IMAGE,
)

IMAGE_ID_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=0, max=255))
IMAGE_SCHEMA = vol.Any(vol.In(IMAGE_KEYWORDS), IMAGE_ID_SCHEMA)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register actions."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_DELETE_IMAGE,
        entity_domain=SENSOR_DOMAIN,
        schema={vol.Required(ATTR_IMAGE): IMAGE_SCHEMA},
        func="async_delete_image",
        supports_response=SupportsResponse.OPTIONAL,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_DOWNLOAD_IMAGE,
        entity_domain=SENSOR_DOMAIN,
        schema={
            vol.Required(ATTR_IMAGE_ID): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=255)
            ),
            vol.Optional(ATTR_SHOW, default=True): cv.boolean,
        },
        func="async_download_image",
        supports_response=SupportsResponse.OPTIONAL,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_UPLOAD_IMAGE,
        entity_domain=SENSOR_DOMAIN,
        schema={vol.Required(ATTR_IMAGE_URL): cv.url},
        func="async_upload_image",
        supports_response=SupportsResponse.ONLY,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SHOW_IMAGE,
        entity_domain=SENSOR_DOMAIN,
        schema={
            vol.Exclusive(ATTR_IMAGE_URL, "image"): cv.url,
            vol.Exclusive(ATTR_IMAGE_PATH, "image"): cv.string,
            vol.Exclusive(ATTR_IMAGE_DATA, "image"): cv.string,
            vol.Optional(ATTR_MAKE_ROOM, default=True): cv.boolean,
            vol.Optional(ATTR_SHOW, default=True): cv.boolean,
        },
        func="async_show_image",
        supports_response=SupportsResponse.OPTIONAL,
    )
