from __future__ import annotations

import reflex as rx

from dlhd_proxy import backend
from dlhd_proxy.components import navbar


class SettingsState(rx.State):
    """State for the application settings page."""

    public_url: str = backend.get_public_url()
    feedback: str = ""
    feedback_type: str = "info"
    env_override: bool = backend.public_url_has_env_override()
    is_saving: bool = False

    def set_public_url(self, value: str) -> None:
        self.public_url = value

    def clear_feedback(self) -> None:
        self.feedback = ""
        self.feedback_type = "info"

    def reset_input(self) -> None:
        self.public_url = ""
        self.clear_feedback()

    async def save(self) -> None:
        submitted = self.public_url
        self.feedback = ""
        self.feedback_type = "info"
        self.is_saving = True
        try:
            updated = backend.update_public_url(submitted)
        except ValueError as exc:
            self.feedback = str(exc)
            self.feedback_type = "error"
        except Exception as exc:  # pragma: no cover - defensive
            self.feedback = f"Failed to update public URL: {exc}"
            self.feedback_type = "error"
        else:
            self.public_url = updated
            self.env_override = backend.public_url_has_env_override()
            if not submitted.strip():
                self.feedback = "Public URL reset to the default server address."
            elif self.env_override:
                self.feedback = (
                    "Public URL saved. Environment overrides remain active "
                    "until the container is restarted without PUBLIC_URL/API_URL."
                )
            else:
                self.feedback = "Public URL updated successfully."
            self.feedback_type = "success"
        finally:
            self.is_saving = False


def _feedback_banner() -> rx.Component:
    colors = {
        "info": "gray",
        "success": "green",
        "error": "red",
    }
    return rx.cond(
        SettingsState.feedback,
        rx.card(
            rx.hstack(
                rx.cond(
                    SettingsState.feedback_type == "success",
                    rx.icon("circle_check", size=22),
                    rx.cond(
                        SettingsState.feedback_type == "error",
                        rx.icon("triangle_alert", size=22),
                        rx.icon("info", size=22),
                    ),
                ),
                rx.text(SettingsState.feedback),
                spacing="3",
                align="center",
            ),
            background_color=rx.color(colors.get(SettingsState.feedback_type, "gray"), 3),
        ),
    )


def _env_warning() -> rx.Component:
    return rx.cond(
        SettingsState.env_override,
        rx.card(
            rx.hstack(
                rx.icon("shield_alert", size=22),
                rx.text(
                    "PUBLIC_URL or API_URL is set for this container. "
                    "These variables override any value saved in the web interface.",
                ),
                align="center",
                spacing="3",
            ),
            background_color=rx.color("yellow", 3),
        ),
    )


@rx.page("/settings")
def settings() -> rx.Component:
    return rx.box(
        navbar(),
        rx.container(
            rx.vstack(
                rx.heading("Application settings", size="7"),
                rx.text(
                    "Configure the public URL used for playlists, the embedded web "
                    "player, and generated logo links. Set this to the address you "
                    "access through a reverse proxy.",
                    color="gray.11",
                ),
                _env_warning(),
                _feedback_banner(),
                rx.vstack(
                    rx.text("Public URL", weight="medium"),
                    rx.input(
                        placeholder="https://tv.example.com", 
                        value=SettingsState.public_url,
                        on_change=SettingsState.set_public_url,
                        size="3",
                    ),
                    rx.text(
                        "Leave blank to revert to the server's default address.",
                        size="2",
                        color="gray.10",
                    ),
                    rx.hstack(
                        rx.button(
                            "Save",
                            on_click=SettingsState.save,
                            loading=SettingsState.is_saving,
                            size="3",
                        ),
                        rx.button(
                            "Reset",
                            on_click=SettingsState.reset_input,
                            variant="soft",
                            color_scheme="gray",
                            size="3",
                        ),
                        spacing="3",
                    ),
                    align="start",
                    spacing="3",
                    padding="1rem",
                    background_color=rx.color("gray", 2),
                    border_radius="lg",
                ),
                rx.vstack(
                    rx.text("Active URL", weight="medium"),
                    rx.code(SettingsState.public_url),
                    align="start",
                    spacing="2",
                ),
                spacing="4",
            ),
            padding_top="7rem",
            padding_bottom="3rem",
        ),
    )
