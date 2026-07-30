"""Error types that the CLI knows how to print without a traceback."""


class CrecError(Exception):
    """Anything we can explain to the user in one line."""


class MissingDependency(CrecError):
    """ffmpeg or yt-dlp is not available and could not be fetched."""


class ExtractorFailure(CrecError):
    """The site broke. Usually means yt-dlp is stale, so it triggers a retry."""


class NoUrl(CrecError):
    """Nothing on the clipboard, no argument, no remembered URL."""


class ImpossibleTarget(CrecError):
    """The requested file size cannot hold a watchable video."""
