import aiohttp
import mimetypes
from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import TextMessageEventContent, MessageType, Format
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from typing import Type, Tuple
from bs4 import BeautifulSoup


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("searxng_on")
        helper.copy("searxng_url")
        helper.copy("searxng_port")
        helper.copy("max_results")


class ImdbBot(Plugin):
    headers = {
            "Sec-GPC": "1",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en,en-US;q=0.5",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0",
            "referer": "https://duckduckgo.com/"
        }

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()

    @command.new(name="imdb", help="IMDb Search")
    @command.argument("title", pass_raw=True, required=True)
    async def search(self, evt: MessageEvent, title: str) -> None:
        await evt.mark_read()
        title = title.strip()
        if not title:
            await evt.respond("Usage: !imdb <title>")
            return

        urls = await self.web_search(title)
        if not urls:
            await evt.reply(f"Failed to find results for *{title}*")
            return

        content = await self.prepare_message(urls)
        if content:
            await evt.reply(content)
        else:
            await evt.reply("Something went wrong when I was preparing summary.")

    async def web_search(self, modifier: str, query: str) -> list[Tuple[str, str]]:
        if self.config["searxng_on"]:
            return await self.searxng_search(query)
        return await self.ddg_search(query)

    async def searxng_search(self, query: str) -> list[Tuple[str, str]]:
        url = self.config["searxng_url"] if self.config["searxng_url"] else "http://127.0.0.1"
        port = self.config["searxng_port"] if self.config["searxng_port"] else 8080
        max_results = self.config["max_results"] if self.config["max_results"] else 4
        params = {
            "q": query,
            "format": "json",
            "engines": "imdb",
            "language": "en"
        }
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            response = await self.http.get(url, params=params, timeout=timeout, allow_redirects=True, raise_for_status=True)
            results = await response.json()
        except aiohttp.ClientError as e:
            self.log.error(f"Web search: Connection failed: {e}")
            return []
        max_results = max_results if len(results) >= max_results else len(results)
        results_short = []
        for i in range(0, max_results):
            results_short.append((results[i]['title'], results[i]['url']))
        return results_short

    async def ddg_search(self, query: str) -> list[Tuple[str, str]]:
        max_results = self.config["max_results"] if self.config["max_results"] else 4
        query = "site:imdb.com " + query
        params = {
            "q": query,
            "kd": "-1",  # Redirect off
            "k1": "-1",  # Ads off
            "kl": "en-us"
        }
        url = f"https://lite.duckduckgo.com/lite/"
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            response = await self.http.get(url, headers=ImdbBot.headers, params=params, timeout=timeout, raise_for_status=True)
            res_text = await response.text()
        except aiohttp.ClientError as e:
            self.log.error(f"Connection failed: {e}")
            return []

        soup = BeautifulSoup(res_text, "html.parser")
        if not soup:
            self.log.error("Failed to parse the source.")
            return []
        links = soup.find_all("a", class_="result-link", limit=max_results)
        if not links:
            self.log.error("Failed to find the link.")
            return []
        results = []
        for link in links:
            # When there are no results, DDG returns a link to Google Search with EOT title
            if link.text == "EOF" and (link["href"].startswith("http://www.google.com/search") or link["href"].startswith("https://www.google.com/search")):
                break
            results.append((link.text, link["href"]))
        return results

    async def ddg_search2(self, query: str) -> str:
        url = f"https://lite.duckduckgo.com/lite/?q=\\+site:imdb.com+{query}"
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            response = await self.http.get(url, headers=ImdbBot.headers, timeout=timeout, allow_redirects=True, raise_for_status=True)
            result = str(response.url)
            if "duckduckgo.com" not in result:
                return result
        except aiohttp.ClientError as e:
            self.log.error(f"Web search: Connection failed: {e}")
        return ""

    async def prepare_message(self, urls: list[Tuple[str, str]]) -> TextMessageEventContent | None:
        main_result = urls[0]
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            response = await self.http.get(main_result[1], headers=ImdbBot.headers, timeout=timeout, raise_for_status=True)
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

        body = (
            f"> ### [{title}]({main_result[1]})\n> {description}  \n"
            f"> \n"
            f"> > **Rating:** {rating} ⭐  \n"
            f"> > **Type:** {video_type}  \n"
            f"> > **Runtime:** {time}  \n"
            f"> > **Age restriction:** {age}  \n"
            f"> > **Tags:** {tags}  \n"
        )

        html = (
            f"<div>"
            f"<blockquote>"
            f"<a href=\"{main_result[1]}\">"
            f"<h3>{title}</h3>"
            f"</a>"
            f"<p>{description}</p>"
            f"<blockquote><b>Rating:</b> {rating} ⭐</blockquote>"
            f"<blockquote><b>Type:</b> {video_type}</blockquote>"
            f"<blockquote><b>Runtime:</b> {time}</blockquote>"
            f"<blockquote><b>Age restriction:</b> {age}</blockquote>"
            f"<blockquote><b>Tags:</b> {tags}</blockquote>"
            f"<img src=\"{image_uri}\" width=\"300\" /><br>"
        )

        if len(urls) > 1:
            body += f"> **Other results:**  \n"
            html += (
                f"<details>"
                f"<br><summary><b>Other results:</b></summary>"
            )
            for i in range (1, len(urls)):
                body += f"> > {i}. [{urls[i][0]}]({urls[i][1]})  \n"
                html += f"<blockquote><a href=\"{urls[i][1]}\">{i}. {urls[i][0]}</a></blockquote>"
            html+= "</details>"

        body += (
            f"> \n"
            f"> **Results from IMDb**"
        )

        html += (
            f"<p><b><sub>Results from IMDb</sub></b></p>"
            f"</blockquote>"
            f"</div>"
        )

        return TextMessageEventContent(
            msgtype=MessageType.NOTICE,
            format=Format.HTML,
            body=body,
            formatted_body=html)

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config
