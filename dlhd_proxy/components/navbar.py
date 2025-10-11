import reflex as rx
from rxconfig import config


def navbar_icons_item(
    text: str,
    icon: str,
    url: str,
    external: bool = False,
    new_tab: bool = False,
) -> rx.Component:
    return rx.link(
        rx.hstack(
            rx.icon(icon, color="white"),
            rx.text(text, size="4", weight="medium", color="white"),
        ),
        href=url,
        is_external=external,
        target="_blank" if new_tab else "_self" if external else None,
    )


def navbar_icons_menu_item(
    text: str,
    icon: str,
    url: str,
    external: bool = False,
    new_tab: bool = False,
) -> rx.Component:
    return rx.link(
        rx.hstack(
            rx.icon(icon, size=24, color="white"),
            rx.text(text, size="3", weight="medium", color="white"),
        ),
        href=url,
        is_external=external,
        target="_blank" if new_tab else "_self" if external else None,
        padding="0.5em",
    )


def navbar(search=None) -> rx.Component:
    return rx.box(
        rx.card(
            rx.desktop_only(
                rx.hstack(
                    rx.vstack(
                        rx.text(
                            config.app_name.replace("_","-"), size="8", weight="bold"
                        ),
                        rx.box(
                            background_color="#fa5252",
                            width="100%",
                            padding="2.5px",
                        ),
                        align_items="center",
                        gap="0",
                        on_click=rx.redirect("/")
                    ),
                    rx.cond(
                        search,
                        search,
                        rx.text(
                            "Watch ",
                            rx.code("live"),
                            " TV channels",
                            align="center",
                            size="4",
                            padding="5px",
                        ),
                    ),
                    rx.hstack(
                        navbar_icons_item("Schedule", "calendar-sync", "/schedule"),
                        navbar_icons_item("Channels", "list-checks", "/channels"),
                        navbar_icons_item("Refresh", "refresh-cw", "/refresh"),
                        navbar_icons_item("Settings", "settings", "/settings"),
                        navbar_icons_item("playlist.m3u8", "file-down", "/playlist"),
                        navbar_icons_item("guide.xml", "file-text", "/guide.xml", True),
                        navbar_icons_item("Logs", "bug", "/logs", True),
                        navbar_icons_item(
                            "Github",
                            "github",
                            "https://github.com/eribbey/dlhd-proxy",
                            True,
                            True,
                        ),
                        spacing="6",
                    ),
                    justify=rx.breakpoints(initial="between"),
                    align_items="center",
                ),
            ),
            rx.mobile_and_tablet(
                rx.vstack(
                    rx.hstack(
                        rx.vstack(
                            rx.text(
                                config.app_name.replace("_","-"), size="7", weight="bold"
                            ),
                            rx.box(
                                background_color="#fa5252",
                                width="100%",
                                padding="2.5px",
                            ),
                            align_items="center",
                            gap="0",
                            on_click=rx.redirect("/")
                        ),
                        rx.tablet_only(
                            rx.cond(
                                search,
                                search,
                                rx.fragment(),
                            ),
                        ),
                        rx.menu.root(
                            rx.menu.trigger(
                                rx.icon("menu", size=30)
                            ),
                            rx.menu.content(
                                navbar_icons_menu_item("Schedule", "calendar-sync", "/schedule"),
                                navbar_icons_menu_item("Channels", "list-checks", "/channels"),
                                navbar_icons_menu_item("Refresh", "refresh-cw", "/refresh"),
                                navbar_icons_menu_item("Settings", "settings", "/settings"),
                                navbar_icons_menu_item("playlist.m3u8", "file-down", "/playlist"),
                                navbar_icons_menu_item("guide.xml", "file-text", "/guide.xml", True),
                                navbar_icons_menu_item("Logs", "bug", "/logs", True),
                                navbar_icons_menu_item(
                                    "Github",
                                    "github",
                                    "https://github.com/eribbey/dlhd-proxy",
                                    True,
                                    True,
                                ),
                            ),
                            justify="end",
                        ),
                        justify=rx.breakpoints(initial="between"),
                        align_items="center",
                        width="100%",
                    ),
                    rx.cond(
                        search,
                        rx.mobile_only(
                            rx.box(
                                search,
                                width="100%",
                            ),
                            width="100%",
                        ),
                        rx.fragment(),
                    ),
                ),
            ),
            padding="1em",
            width="100%",
        ),
        padding="1rem",
        position="fixed",
        top="0px",
        z_index="2",
        width="100%",
    )
