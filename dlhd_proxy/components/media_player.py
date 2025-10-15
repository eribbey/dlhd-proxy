import reflex as rx
from reflex.components.component import NoSSRComponent
from reflex.utils.imports import ImportVar


class MediaPlayer(NoSSRComponent):
    library = "/public/player"
    lib_dependencies: list[str] = ["@vidstack/react@next", "hls.js@^1.5.0"]
    tag = "DlhdProxyMediaPlayer"
    title: rx.Var[str]
    src: rx.Var[str]
    autoplay: bool = True

    def _get_imports(self):  # noqa: D401 - internal override to fix build tooling.
        """Get imports without duplicating the media player tag."""

        imports = super()._get_imports()

        target_keys = {key for key in (self.library, self._get_import_name()) if key}
        for key in target_keys:
            if key not in imports:
                continue

            imports[key] = [imp for imp in imports[key] if imp.tag != self.tag]

            if not any(imp.tag is None and not imp.render for imp in imports[key]):
                imports[key].append(ImportVar(tag=None, render=False))

        return imports
