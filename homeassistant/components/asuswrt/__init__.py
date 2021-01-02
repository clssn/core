"""Support for ASUSWRT devices."""
import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_SENSORS,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import HomeAssistantType

from .const import (
    CONF_DNSMASQ,
    CONF_INTERFACE,
    CONF_REQUIRE_IP,
    CONF_SSH_KEY,
    DATA_ASUSWRT,
    DEFAULT_DNSMASQ,
    DEFAULT_INTERFACE,
    DEFAULT_SSH_PORT,
    DOMAIN,
    MODE_AP,
    MODE_ROUTER,
    PROTOCOL_SSH,
    PROTOCOL_TELNET,
    SENSOR_TYPES,
)
from .router import AsusWrtRouter

PLATFORMS = ["device_tracker", "sensor"]

CONF_PUB_KEY = "pub_key"
SECRET_GROUP = "Password or SSH Key"

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    vol.All(
        cv.deprecated(DOMAIN),
        {
            DOMAIN: vol.Schema(
                {
                    vol.Required(CONF_HOST): cv.string,
                    vol.Required(CONF_USERNAME): cv.string,
                    vol.Optional(CONF_PROTOCOL, default=PROTOCOL_SSH): vol.In(
                        [PROTOCOL_SSH, PROTOCOL_TELNET]
                    ),
                    vol.Optional(CONF_MODE, default=MODE_ROUTER): vol.In(
                        [MODE_ROUTER, MODE_AP]
                    ),
                    vol.Optional(CONF_PORT, default=DEFAULT_SSH_PORT): cv.port,
                    vol.Optional(CONF_REQUIRE_IP, default=True): cv.boolean,
                    vol.Exclusive(CONF_PASSWORD, SECRET_GROUP): cv.string,
                    vol.Exclusive(CONF_SSH_KEY, SECRET_GROUP): cv.isfile,
                    vol.Exclusive(CONF_PUB_KEY, SECRET_GROUP): cv.isfile,
                    vol.Optional(CONF_SENSORS): vol.All(
                        cv.ensure_list, [vol.In(SENSOR_TYPES)]
                    ),
                    vol.Optional(CONF_INTERFACE, default=DEFAULT_INTERFACE): cv.string,
                    vol.Optional(CONF_DNSMASQ, default=DEFAULT_DNSMASQ): cv.string,
                }
            )
        },
    ),
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass, config):
    """Set up the AsusWrt integration."""
    conf = config.get(DOMAIN)
    if conf is None:
        return True

    # save the options from config yaml
    options = {}
    mode = conf.get(CONF_MODE, MODE_ROUTER)
    for name, value in conf.items():
        if name in ([CONF_DNSMASQ, CONF_INTERFACE, CONF_REQUIRE_IP]):
            if name == CONF_REQUIRE_IP and mode != MODE_AP:
                continue
            options[name] = value
    hass.data[DOMAIN] = {"yaml_options": options}

    # check if already configured
    domains_list = hass.config_entries.async_domains()
    if DOMAIN in domains_list:
        return True

    # remove not required config keys
    pub_key = conf.pop(CONF_PUB_KEY, "")
    if pub_key:
        conf[CONF_SSH_KEY] = pub_key

    conf.pop(CONF_REQUIRE_IP, True)
    conf.pop(CONF_SENSORS, {})
    conf.pop(CONF_INTERFACE, "")
    conf.pop(CONF_DNSMASQ, "")

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
        )
    )

    return True


async def async_setup_entry(hass: HomeAssistantType, entry: ConfigEntry):
    """Set up AsusWrt platform."""

    # import options from yaml if empty
    yaml_options = hass.data.get(DOMAIN, {}).pop("yaml_options", {})
    if not entry.options and yaml_options:
        hass.config_entries.async_update_entry(entry, options=yaml_options)

    router = AsusWrtRouter(hass, entry)
    await router.setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][DATA_ASUSWRT] = router

    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, platform)
        )

    hass.data[DOMAIN]["update_listener"] = entry.add_update_listener(update_listener)

    async def async_close_connection(event):
        """Close AsusWrt connection on HA Stop."""
        await router.close()

    hass.data[DOMAIN]["stop_listener"] = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STOP, async_close_connection
    )

    return True


async def async_unload_entry(hass: HomeAssistantType, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
            ]
        )
    )
    if unload_ok:
        options_listener = hass.data[DOMAIN].pop("update_listener")
        options_listener()
        stop_listener = hass.data[DOMAIN].pop("stop_listener")
        stop_listener()

        router = hass.data[DOMAIN].pop(DATA_ASUSWRT)
        await router.close()

    return unload_ok


async def update_listener(hass: HomeAssistantType, entry: ConfigEntry):
    """Update when config_entry options update."""
    router = hass.data[DOMAIN][DATA_ASUSWRT]

    if router.update_options(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)
