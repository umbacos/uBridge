"""UI and YAML setup for the serial panel."""

try:
    import probatio as vol
except ImportError:
    import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .dashboards import dashboard_choices
from .display.const import BAUD_RATES, DEFAULT_BAUD, DOMAIN


def schema(defaults: dict, choices=None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional("brightness", default=defaults.get("brightness", 80)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=100)
            ),
            vol.Optional("idle_enabled", default=defaults.get("idle_enabled", True)): bool,
            vol.Optional("idle_dim_seconds", default=defaults.get("idle_dim_seconds", 60)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=86400)
            ),
            vol.Optional("idle_dim_percent", default=defaults.get("idle_dim_percent", 50)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=100)
            ),
            vol.Optional("idle_off_seconds", default=defaults.get("idle_off_seconds", 60)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=86400)
            ),
            vol.Required("port", default=defaults.get("port", "/dev/serial/by-id/")): str,
            vol.Required("baudrate", default=defaults.get("baudrate", DEFAULT_BAUD)): vol.In(
                BAUD_RATES
            ),
            vol.Required("dashboards", default=defaults.get("dashboards", [])): SelectSelector(
                SelectSelectorConfig(options=choices or [], multiple=True, mode="dropdown")
            ),
            vol.Optional("idle_mode", default=defaults.get("idle_mode", "dim")): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        {"value": "dim", "label": "Dim, then turn off"},
                        {"value": "dashboard", "label": "Show a dashboard"},
                    ]
                )
            ),
            vol.Optional(
                "idle_dashboard", default=defaults.get("idle_dashboard", "")
            ): SelectSelector(
                SelectSelectorConfig(options=[{"value": "", "label": "None"}, *(choices or [])])
            ),
            vol.Optional("idle_brightness", default=defaults.get("idle_brightness", 50)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=100)
            ),
        }
    )


def valid_input(values, choices):
    available = {option["value"] for option in choices}
    selected = values.get("dashboards", [])
    return (
        bool(values.get("port", "").strip())
        and bool(selected)
        and all(url in available for url in selected)
        and (
            not values.get("idle_enabled", True)
            or values.get("idle_mode", "dim") != "dashboard"
            or values.get("idle_dashboard") in available
        )
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not valid_input(user_input, dashboard_choices(self.hass)):
                errors["base"] = "invalid_settings"
            else:
                user_input = {
                    **user_input,
                    "port": user_input["port"].strip(),
                }
                if any(
                    e.options.get("port", e.data.get("port")) == user_input["port"]
                    for e in self.hass.config_entries.async_entries(DOMAIN)
                ):
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(user_input["port"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="TJC HMI", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=schema(user_input or {}, dashboard_choices(self.hass)),
            errors=errors,
        )

    async def async_step_import(self, user_input):
        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            if not valid_input(user_input, dashboard_choices(self.hass)):
                return self.async_show_form(
                    step_id="init",
                    data_schema=schema(user_input, dashboard_choices(self.hass)),
                    errors={"base": "invalid_settings"},
                )
            user_input = {
                **user_input,
                "port": user_input["port"].strip(),
            }
            if any(
                e.entry_id != self.config_entry.entry_id
                and e.options.get("port", e.data.get("port")) == user_input["port"]
                for e in self.hass.config_entries.async_entries(DOMAIN)
            ):
                return self.async_abort(reason="already_configured")
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=schema(
                {**self.config_entry.data, **self.config_entry.options},
                dashboard_choices(self.hass),
            ),
        )
