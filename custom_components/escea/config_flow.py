"""Config flow for escea."""

import asyncio
from contextlib import suppress
import logging
from typing import Any

import voluptuous as vol
from pescea import Controller

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import homeassistant.helpers.config_validation as cv

from .const import (
    DISPATCH_CONTROLLER_DISCOVERED,
    DOMAIN,
    TIMEOUT_DISCOVERY,
    TIMEOUT_DISCOVERY_MANUAL,
)
from .discovery import async_start_discovery_service, async_stop_discovery_service

_LOGGER = logging.getLogger(__name__)

SETUP_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("setup_mode", default="automatic"): vol.In(
            {
                "automatic": "Automatic Discovery",
                "manual": "Manual IP Address",
            }
        )
    }
)


class EsceaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Escea."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self.discovered_controllers: list[Controller] = []

    async def async_step_user(self, user_input):
        """Handle the initial step."""
        if user_input is not None:
            if user_input["setup_mode"] == "automatic":
                return await self.async_step_discovery()
            else:
                return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=SETUP_MODE_SCHEMA,
        )

    async def async_step_discovery(self, user_input: dict[str, Any] | None = None):
        """Handle automatic discovery."""
        return await self._handle_discovery(user_input, ip_addr=None)

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        """Handle manual IP address entry."""
        if user_input is not None:
            # User provided IP address, now discover with that specific IP
            return await self._handle_discovery(user_input, ip_addr=user_input["host"])

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required("host"): cv.string,
                }
            ),
        )

    async def _handle_discovery(self, user_input, ip_addr: str | None = None):
        """Perform device discovery and handle user selection."""
        errors = {}

        if user_input is not None and "controller" in user_input:
            # User selected a discovered controller
            selected_controller = next(
                (
                    ctrl
                    for ctrl in self.discovered_controllers
                    if str(ctrl.device_uid) == user_input["controller"]
                ),
                None,
            )

            if selected_controller:
                unique_id = str(selected_controller.device_uid)
                _LOGGER.debug("Setting unique ID to: escea_%s", unique_id)
                await self.async_set_unique_id(f"escea_{unique_id}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Escea Fireplace {selected_controller.device_uid} ({selected_controller.device_ip})",
                    data={"host": str(selected_controller.device_ip)},
                )

        controller_ready = asyncio.Event()
        
        _LOGGER.info("Starting discovery (manual IP: %s)", ip_addr or "automatic")

        @callback
        def dispatch_discovered(_):
            controller_ready.set()

        remove_handler = async_dispatcher_connect(
            self.hass, DISPATCH_CONTROLLER_DISCOVERED, dispatch_discovered
        )

        discovery_service = await async_start_discovery_service(self.hass, ip_addr)

        # Use longer timeout for manual discovery
        timeout_duration = TIMEOUT_DISCOVERY_MANUAL if ip_addr else TIMEOUT_DISCOVERY

        with suppress(asyncio.TimeoutError):
            async with asyncio.timeout(timeout_duration):
                await controller_ready.wait()

        # controllers: Dict[serial, Controller]
        self.discovered_controllers = list(discovery_service.controllers.values())

        _LOGGER.debug(
            "Discovery completed. Found %d controllers: %s",
            len(self.discovered_controllers),
            self.discovered_controllers,
        )

        remove_handler()
        await async_stop_discovery_service(self.hass)

        if not self.discovered_controllers:
            _LOGGER.debug("No controllers found")
            if ip_addr:
                # Manual IP entry - show error and return to manual form
                errors["host"] = "cannot_connect"
                return self.async_show_form(
                    step_id="manual",
                    data_schema=vol.Schema(
                        {
                            vol.Required("host", default=ip_addr): cv.string,
                        }
                    ),
                    errors=errors,
                )
            else:
                # Automatic discovery - go back to user step with error
                errors["base"] = "no_devices_found"
                return self.async_show_form(
                    step_id="user",
                    data_schema=SETUP_MODE_SCHEMA,
                    errors=errors,
                )

        controller_options = {
            str(
                controller.device_uid
            ): f"{controller.device_ip} ({controller.device_uid})"
            for controller in self.discovered_controllers
        }

        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema(
                {vol.Required("controller"): vol.In(controller_options)}
            ),
        )
