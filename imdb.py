import aiohttp
import mimetypes
from enum import Enum
from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import TextMessageEventContent, MessageType, Format
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from typing import Type, Tuple
from bs4 import BeautifulSoup


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("max_results")


class ImdbBot(Plugin):
    headers = {
            "Sec-GPC": "1",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en,en-US;q=0.5",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0"
        }
    QueryType = Enum("QueryType", [("title", 1), ("person", 2)])

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()

    @command.new(name="imdb", help="IMDb Search - search for titles of movies and TV series", require_subcommand=False, arg_fallthrough=False)
    @command.argument("title", pass_raw=True, required=True)
    async def imdb(self, evt: MessageEvent, title: str) -> None:
        await evt.mark_read()
        title = title.strip()
        if not title:
            await evt.reply("**Usage:**  \n"
                            "!imdb <title>  \n"
                            "!imdb person <name>")
            return

        urls = await self.imdb_search(title, self.QueryType.title)
        if not urls:
            await evt.reply(f"Failed to find results for *{title}*")
            return

        content = await self.prepare_title_message(urls)
        if content:
            await evt.reply(content)
        else:
            await evt.reply("Something went wrong when I was preparing summary.")

    @imdb.subcommand("person", help="Search for people")
    @command.argument("name", pass_raw=True, required=True)
    async def imdb_people(self, evt: MessageEvent, name: str) -> None:
        await evt.mark_read()
        name = name.strip()
        if not name:
            await evt.reply("**Usage:**  \n"
                            "!imdb person <name>")
            return

        urls = await self.imdb_search(name, self.QueryType.person)
        if not urls:
            await evt.reply(f"Failed to find results for *{name}*")
            return

        content = await self.prepare_character_message(urls)
        if content:
            await evt.reply(content)
        else:
            await evt.reply("Something went wrong when I was preparing summary.")

    async def imdb_search(self, query: str, query_type: QueryType) -> list[Tuple[str, str, str]]:
        query = query.replace(" ", "_").lower()
        max_results = self.get_max_results()
        api_url = f"https://v2.sg.media-imdb.com/suggestion/{query[0]}/{query}.json"
        title_types = ["tvSeries", "short", "movie", "tvMiniSeries"]
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            response = await self.http.get(api_url, headers=ImdbBot.headers, timeout=timeout, raise_for_status=True)
            response_json = await response.json()
        except aiohttp.ClientError as e:
            self.log.error(f"Connection failed: {e}")
            return []
        if query_type == self.QueryType.title:
            base_url = "https://www.imdb.com/title/{id}/"
            results = [elem for elem in response_json["d"] if elem.get("qid", "") in title_types]
        else:
            base_url = "https://www.imdb.com/name/{id}/"
            results = [elem for elem in response_json["d"] if elem["id"][:2] == "nm"]
        max_results = max_results if len(results) >= max_results else len(results)
        results_short = []
        for i in range(0, max_results):
            if query_type == self.QueryType.title:
                additional_info = results[i].get("q","")
            else:
                additional_info = results[i].get("s", "-")
            results_short.append((results[i]["l"], additional_info, base_url.format(id=results[i]["id"])))
        return results_short

    async def prepare_title_message(self, urls: list[Tuple[str, str, str]]) -> TextMessageEventContent | None:
        main_result = urls[0]
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            response = await self.http.get(main_result[2], headers=ImdbBot.headers, timeout=timeout, raise_for_status=True)
            text = await response.text()
        except aiohttp.ClientError as e:
            self.log.error(f"Scraping IMDb: Connection failed: {e}")
            return None

        soup = BeautifulSoup(text, "html.parser")
        if soup.head is None:
            return None
        info = soup.head.find("meta", property="og:title")
        info = info["content"] if info else ""
        info = info.split("|") if "|" in info else ""
        title = info[0].strip() if info else ""
        tags = info[1].strip() if info else ""
        title_rating = title.split("⭐") if "⭐" in title else ""
        rating = title_rating[1] + "/10" if title_rating else "-/10"
        title = title_rating[0] if title_rating else ""
        video_type = main_result[1] if main_result[1] != "feature" else "Movie"
        description = soup.head.find("meta", attrs={"name": "description"})
        description = description["content"] if description else ""
        time_age = soup.head.find("meta", property="og:description")
        time_age = time_age["content"] if time_age else ""
        time_age = time_age.split("|") if "|" in time_age else ""
        time = time_age[0].strip() if time_age else "-"
        age = time_age[1].strip() if time_age else "-"
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
            f"> ### [{title}]({main_result[2]})\n> {description}  \n"
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
            f"<a href=\"{main_result[2]}\">"
            f"<h3>{title}</h3>"
            f"</a>"
            f"<p>{description}</p>"
            f"<blockquote><b>Rating:</b> {rating} ⭐</blockquote>"
            f"<blockquote><b>Type:</b> {video_type}</blockquote>"
            f"<blockquote><b>Runtime:</b> {time}</blockquote>"
            f"<blockquote><b>Age restriction:</b> {age}</blockquote>"
            f"<blockquote><b>Tags:</b> {tags}</blockquote>"
        )
        if image_uri:
            html += f"<img src=\"{image_uri}\" width=\"300\" /><br>"

        if len(urls) > 1:
            body += f"> **Other results:**  \n"
            html += (
                f"<details>"
                f"<br><summary><b>Other results:</b></summary>"
            )
            for i in range (1, len(urls)):
                video_type_other = main_result[1] if main_result[1] != "feature" else "Movie"
                body += f"> > {i}. [{urls[i][0]}]({urls[i][2]}) ({video_type_other}) \n"
                html += f"<blockquote>{i}. <a href=\"{urls[i][2]}\">{urls[i][0]}</a> ({video_type_other})</blockquote>"
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

    async def prepare_character_message(self, urls: list[Tuple[str, str, str]]) -> TextMessageEventContent | None:
        main_result = urls[0]
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            response = await self.http.get(main_result[2], headers=ImdbBot.headers, timeout=timeout, raise_for_status=True)
            text = await response.text()
        except aiohttp.ClientError as e:
            self.log.error(f"Scraping IMDb: Connection failed: {e}")
            return None

        soup = BeautifulSoup(text, "html.parser")
        if soup.head is None:
            return None
        info = soup.head.find("meta", property="og:title")
        info = info["content"].split("|") if info else ""
        name = info[0].strip() if info else "-"
        roles = info[1].strip() if len(info) > 1 else "-"
        description = soup.find("div", attrs={"data-testid": "bio-content"})
        if description:
            for line_break in description.find_all("br"):
                line_break.replace_with("###")
            description = list(filter(None, description.get_text().split("###")))
            description = [s.replace("\n", " ") for s in description]
        else:
            description = [""]
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
            f"> ### [{name}]({main_result[2]})  \n> {"  \n>  \n> ".join(description)}  \n"
            f"> \n"
            f"> > **Roles:** {roles}  \n"
        )

        html = (
            f"<div>"
            f"<blockquote>"
            f"<a href=\"{main_result[2]}\">"
            f"<h3>{name}</h3>"
            f"</a>"
            f"<p>{description[0]}</p>"
        )
        if len(description) > 1:
            html += (
                f"<details>"
                f"<br><summary><b>...</b></summary>"
                f"<p>{"<br><br>".join(description[1:])}</p>"
                f"</details>"
        )
        html += f"<blockquote><b>Roles:</b> {roles}</blockquote>"
        if image_uri:
            html += f"<img src=\"{image_uri}\" width=\"300\" /><br>"

        if len(urls) > 1:
            body += f"> **Other results:**  \n"
            html += (
                f"<details>"
                f"<br><summary><b>Other results:</b></summary>"
            )
            for i in range (1, len(urls)):
                body += f"> > {i}. [{urls[i][0]}]({urls[i][2]}) Known for: {urls[i][1]} \n"
                html += f"<blockquote>{i}. <a href=\"{urls[i][2]}\">{urls[i][0]}</a> Known for: {urls[i][1]}</blockquote>"
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

    def get_max_results(self) -> int:
        try:
            max_results = int(self.config.get("max_results", 4))
        except ValueError:
            self.log.error("Incorrect 'max_results' config value. Setting default value of 4.")
            max_results = 4
        return max_results

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config
