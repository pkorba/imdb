import aiohttp
import mimetypes
from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import TextMessageEventContent, MessageType, Format
from bs4 import BeautifulSoup


class ImdbBot(Plugin):
    headers = {
            "Sec-GPC": "1",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "pl,en-US;q=0.7,en;q=0.3",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64; rv:138.0) Gecko/20100101 Firefox/138.0",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "referer": "https://duckduckgo.com/"
        }

    @command.new(name="imdb", help="IMDb Search")
    @command.argument("title", pass_raw=True, required=True)
    async def search(self, evt: MessageEvent, title: str) -> None:
        await evt.mark_read()
        title = title.strip()
        if not title:
            await evt.respond("Usage: !imdb <title>")
            return

        url = await self.web_search("site:imdb.com", title)
        if not url or url == "https://www.imdb.com/":
            await evt.reply(f"Failed to find results for *{title}*")
            return

        content = await self.prepare_message(url)
        if content:
            await evt.reply(content)
        else:
            await evt.reply("Something went wrong when I was preparing summary.")

    async def web_search(self, modifier: str, query: str) -> str:
        url = f"https://lite.duckduckgo.com/lite/?q=\\+{modifier}+{query}"
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            response = await self.http.get(url, headers=ImdbBot.headers, timeout=timeout, allow_redirects=True, raise_for_status=True)
            result = str(response.url)
            if "duckduckgo.com" not in result:
                return result
        except aiohttp.ClientError as e:
            self.log.error(f"Web search: Connection failed: {e}")
        return ""

    async def prepare_message(self, url: str) -> TextMessageEventContent | None:
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            response = await self.http.get(url, headers=ImdbBot.headers, timeout=timeout, raise_for_status=True)
            text = await response.text()
        except aiohttp.ClientError as e:
            self.log.error(f"Scraping IMDb: Connection failed: {e}")
            return None

        soup = BeautifulSoup(text, "html.parser")
        if soup.head is None:
            return None
        info = soup.head.find("meta", property="og:title")
        info = info["content"].split("⭐") if info else ""
        title = info[0].strip() if info else "-"
        info = info[1].split("|") if info else ["-", "-"]
        rating = info[0].strip() + "/10"
        tags = info[1].strip()
        video_type = soup.head.find("meta", property="og:type")
        video_type = video_type["content"] if video_type else ""
        video_type = "Movie" if video_type == "video.movie" else "TV Series"
        description = soup.head.find("meta", attrs={"name": "description"})
        description = description["content"] if description else "-"
        time_age = soup.head.find("meta", property="og:description")
        time_age = time_age["content"].split("|") if time_age else ""
        time = time_age[0].strip() if time_age else "-"
        age = time_age[1].strip() if len(time_age) == 2 else "-"
        image = soup.head.find("meta", property="og:image")
        image = image["content"] if image else ""
        image_uri = ""

        try:
            response = await self.http.get(image, raise_for_status=True)
            data = await response.read()
            content_type = response.content_type
            extension = mimetypes.guess_extension(content_type)
            image_uri = await self.client.upload_media(
                data=data,
                mime_type=content_type,
                filename=f"image{extension}",
                size=len(data))
        except aiohttp.ClientError as e:
            self.log.error(f"Preparing image: Connection failed: {image}: {e}")
        except Exception as e:
            self.log.error(f"Preparing image: Unknown error: {image}: {e}")

        body = (f"> ### [{title}](url)\n> {description}  \n"
                f"> \n"
                f"> > **Rating:** {rating} ⭐  \n"
                f"> > **Runtime:** {time}  \n"
                f"> > **Age restriction:** {age}  \n"
                f"> > **Tags:** {tags}  \n"
                f"> > \n"
                f"> > **{video_type} ・ Results from IMDb**")

        html = (f"<div>"
                    f"<blockquote>"
                    f"<a href=\"{url}\">"
                        f"<h3>{title}</h3>"
                    f"</a>"
                    f"<p>{description}</p>"
                    f"<blockquote><b>Rating:</b> {rating} ⭐</blockquote>"
                    f"<blockquote><b>Runtime:</b> {time}</blockquote>"
                    f"<blockquote><b>Age restriction:</b> {age}</blockquote>"
                    f"<blockquote><b>Tags:</b> {tags}</blockquote>"
                    f"<img src=\"{image_uri}\" width=\"300\" /><br>"
                    f"<p><b><sub>{video_type} ・ Results from IMDb</sub></b></p>"
                    f"</blockquote>"
                f"</div>")

        content = TextMessageEventContent(
            msgtype=MessageType.NOTICE,
            format=Format.HTML,
            body=body,
            formatted_body=html)
        return content
