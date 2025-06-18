from dataclasses import dataclass


@dataclass
class ImdbTitleData:
    title: str = "",
    score: str = "",
    votes: str = "",
    tags: list[str] = [],
    video_type: str = "",
    description: str = "",
    duration: str = "",
    rating: str = "",
    image: str = "",
    seasons: int = 0


@dataclass
class ImdbPersonData:
    name: str = "",
    roles: str = "",
    description: list[str] = [],
    image: str  = ""
