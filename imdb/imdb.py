import asyncio
import aiohttp
import mimetypes
from enum import Enum
from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import TextMessageEventContent, MessageType, Format
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from typing import Type, Tuple
from lxml import html
from .resources.datastructures import ImdbTitleData, ImdbPersonData


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("max_results")


class ImdbBot(Plugin):
    headers = {
            "Sec-GPC": "1",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en,en-US;q=0.5",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"
        }
    QueryType = Enum("QueryType", [("title", 1), ("person", 2)])

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()

    @command.new(name="imdb", help="Search for titles of movies and TV series on IMDb", require_subcommand=False, arg_fallthrough=False)
    @command.argument("title", pass_raw=True, required=True)
    async def imdb(self, evt: MessageEvent, title: str) -> None:
        await evt.mark_read()
        title = title.strip()
        if not title:
            await evt.reply("> **Usage:**  \n"
                            "> !imdb <title>  \n"
                            "> !imdb person <name>")
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

    @imdb.subcommand("person", help="Search for people on IMDb")
    @command.argument("name", pass_raw=True, required=True)
    async def imdb_people(self, evt: MessageEvent, name: str) -> None:
        await evt.mark_read()
        name = name.strip()
        if not name:
            await evt.reply("> **Usage:**  \n"
                            "> !imdb person <name>")
            return

        urls = await self.imdb_search(name, self.QueryType.person)
        if not urls:
            await evt.reply(f"Failed to find results for *{name}*")
            return

        content = await self.prepare_person_message(urls)
        if content:
            await evt.reply(content)
        else:
            await evt.reply("Something went wrong when I was preparing summary.")

    async def imdb_search(self, query: str, query_type: QueryType) -> list[Tuple[str, str, str]]:
        """
        Perform a search in IMDb database
        :param query: search query
        :param query_type: title or person
        :return: list of Tuples. Each tuple contains three values:
            - title/name
            - optional information about type of result (e.g. TV series)
            - URL of the result
        """
        query = query.replace(" ", "_").lower()
        api_url = f"https://v2.sg.media-imdb.com/suggestion/{query[0]}/{query}.json"
        title_types = ["tvSeries", "short", "movie", "tvMiniSeries"]
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            response = await self.http.get(api_url, headers=self.headers, timeout=timeout, raise_for_status=True)
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
        max_results = min(self.get_max_results(), len(results))
        results_short = []
        for i in range(0, max_results):
            if query_type == self.QueryType.title:
                additional_info = results[i].get("q", "")
            else:
                additional_info = results[i].get("s", "-")
            results_short.append((results[i]["l"], additional_info, base_url.format(id=results[i]["id"])))
        return results_short

    def get_title_data(self, text: str, video_type: str) -> ImdbTitleData | None:
        """
        Extract information about movie/TV series from text
        :param text: website content
        :param video_type: type of video content
        :return: information about movie/TV series
        """
        data = ImdbTitleData()
        page = html.fromstring(text)
        if page is None:
            return None
        score = page.xpath("//a[@aria-label='View User Ratings']")
        score = score[0].text_content().split("/10") if score else ["", ""]
        data.votes = score[1]
        data.score = score[0] + "/10"
        tags = page.xpath("//div[@data-testid='interests']//a")
        if tags is not None:
            data.tags = [tag.text_content() for tag in tags]
        info = page.xpath("//meta[@property='og:title']/@content")
        info = info[0] if info else ""
        title = info[:info.index("|")] if "|" in info else info
        title_score = title.split("⭐") if "⭐" in title else [title]
        data.title = title_score[0]
        data.video_type = video_type if video_type != "feature" else "Movie"
        description = page.xpath("//meta[@name='description']/@content")
        data.description = description[0] if description else ""
        duration_rating = page.xpath("//meta[@property='og:description']/@content")
        duration_rating = duration_rating[0] if duration_rating else ""
        duration_rating = duration_rating.split("|") if "|" in duration_rating else ""
        data.duration = duration_rating[0].strip() if duration_rating else "-"
        data.rating = duration_rating[1].strip() if duration_rating else "-"
        image = page.xpath("//meta[@property='og:image']/@content")
        data.image = image[0] if image else ""
        if video_type[:2].upper() == "TV":
            seasons = page.xpath("//select[@id='browse-episodes-season']/@aria-label")
            seasons = seasons[0].split() if seasons else None
            try:
                data.seasons = int(seasons[0]) if seasons else 1
            except ValueError:
                pass
        return data

    async def prepare_title_message(self, urls: list[Tuple[str, str, str]]) -> TextMessageEventContent | None:
        """
        Prepare message about movie/TV series
        :param urls: list of tuples. Each tuple contains three values:
            - title/name
            - optional information about type of result (e.g. TV series)
            - URL of the result
        :return: message ready to be sent to the user
        """
        main_result = urls[0]
        text = await self.get_page_text(main_result[2])
        if not text:
            return None
        title_data = await asyncio.get_running_loop().run_in_executor(None, self.get_title_data, text, main_result[1])
        if not title_data:
            return None
        image_url = await self.get_resized_image_url(title_data.image)
        image_mxc = await self.get_matrix_image_url(image_url)

        body = f"> ### [{title_data.title}]({main_result[2]})\n> {title_data.description}  \n>  \n"
        html_msg = (
            f"<blockquote><table><tr><td>"
            f"<a href=\"{main_result[2]}\"><h3>{title_data.title}</h3></a>"
            f"<p>{title_data.description}</p>"
            f"</td><td>"
        )
        if image_mxc:
            html_msg += f"<br><img src=\"{image_mxc}\" height=\"200\" /></p>"
        html_msg += "</td></tr><tr><td><br>"
        if title_data.score != "/10":
            body += f"> > **Score:** ⭐ {title_data.score} - {title_data.votes} votes  \n>  \n"
            html_msg += f"<blockquote><b>Score:</b> ⭐ {title_data.score} - {title_data.votes} votes</blockquote>"
        else:
            body += "> > **Not released yet**  \n>  \n"
            html_msg += "<blockquote><b>Not released yet</b></blockquote>"

        body += f"> > **Type:** {title_data.video_type}  \n>  \n"
        html_msg += f"<blockquote><b>Type:</b> {title_data.video_type}</blockquote>"
        if title_data.seasons:
            body_seasons = [f"[{i + 1}]({main_result[2]}episodes/?season={i + 1})" for i in range(0, title_data.seasons)]
            html_seasons = [f"<a href=\"{main_result[2]}episodes/?season={i + 1}\">{i + 1}</a>" for i in range(0, title_data.seasons)]
            body += f"> > **Seasons:** {', '.join(body_seasons)}  \n>  \n"
            html_msg += f"<blockquote><b>Seasons:</b> {', '.join(html_seasons)}</blockquote>"
        body += (
            f"> > **Duration:** {title_data.duration}  \n>  \n"
            f"> > **Rating:** {title_data.rating}  \n>  \n"
            f"> > **Tags:** {", ".join(title_data.tags)}  \n>  \n"
        )
        html_msg += (
            f"<blockquote><b>Duration:</b> {title_data.duration}</blockquote>"
            f"<blockquote><b>Rating:</b> {title_data.rating}</blockquote>"
            f"<blockquote><b>Tags:</b> {", ".join(title_data.tags)}</blockquote>"
            f"</td><td>"
        )
        if len(urls) > 1:
            body += "> **Other results:**  \n"
            html_msg += "<br><b>Other results:</b>"

            for i in range(1, len(urls)):
                video_type_other = main_result[1] if main_result[1] != "feature" else "Movie"
                body += f"> > {i}. [{urls[i][0]}]({urls[i][2]}) ({video_type_other}) \n>  \n"
                html_msg += f"<blockquote>{i}. <a href=\"{urls[i][2]}\">{urls[i][0]}</a> ({video_type_other})</blockquote>"
        html_msg += "</td></tr></table>"
        body += (
            "> \n"
            "> **Results from IMDb**"
        )
        html_msg += (
            "<p><b><sub>Results from IMDb</sub></b></p>"
            "</blockquote>"
        )

        return TextMessageEventContent(
            msgtype=MessageType.NOTICE,
            format=Format.HTML,
            body=body,
            formatted_body=html_msg)

    def get_person_data(self, text: str) -> ImdbPersonData | None:
        """
        Extract information about a person from text
        :param text: website content
        :return: information about a person
        """
        data = ImdbPersonData()
        page = html.fromstring(text)
        if page is None:
            return None
        info = page.xpath("//meta[@property='og:title']/@content")
        info = info[0].split("|") if info else ""
        data.name = info[0].strip() if info else "-"
        data.roles = info[1].strip() if len(info) > 1 else "-"
        description = page.xpath("//div[@data-testid='bio-content']")
        description = description[0] if description else None
        if description is not None:
            for line_break in description.xpath("//br"):
                line_break.tail = "###" + line_break.tail if line_break.tail else "###"
            description = list(filter(None, description.text_content().split("###")))
            data.description = [s.replace("\n", " ") for s in description]
        else:
            data.description = [""]
        image = page.xpath("//meta[@property='og:image']/@content")
        data.image = image[0] if image else ""
        return data

    async def prepare_person_message(self, urls: list[Tuple[str, str, str]]) -> TextMessageEventContent | None:
        main_result = urls[0]
        text = await self.get_page_text(main_result[2])
        if not text:
            return None
        person_data = await asyncio.get_event_loop().run_in_executor(None, self.get_person_data, text)
        if not person_data:
            return None
        image_url = await self.get_resized_image_url(person_data.image)
        image_mxc = await self.get_matrix_image_url(image_url)

        body = (
            f"> ### [{person_data.name}]({main_result[2]})  \n> {"  \n>  \n> ".join(person_data.description)}  \n> \n"
            f"> > **Roles:** {person_data.roles}  \n>  \n"
        )

        html_msg = (
            f"<div>"
            f"<blockquote>"
            f"<a href=\"{main_result[2]}\">"
            f"<h3>{person_data.name}</h3>"
            f"</a>"
            f"<p>{person_data.description[0]}</p>"
        )
        if len(person_data.description) > 1:
            html_msg += (
                f"<details>"
                f"<br><summary><b>...</b></summary>"
                f"<p>{"<br><br>".join(person_data.description[1:])}</p>"
                f"</details>"
            )
        html_msg += f"<blockquote><b>Roles:</b> {person_data.roles}</blockquote>"
        if image_mxc:
            html_msg += f"<img src=\"{image_mxc}\" width=\"300\" height=\"444\" /><br>"

        if len(urls) > 1:
            body += "> **Other results:**  \n"
            html_msg += (
                "<p><details>"
                "<summary><b>Other results:</b></summary>"
            )
            for i in range(1, len(urls)):
                body += f"> > {i}. [{urls[i][0]}]({urls[i][2]}) Known for: {urls[i][1]}  \n>  \n"
                html_msg += f"<blockquote>{i}. <a href=\"{urls[i][2]}\">{urls[i][0]}</a> Known for: {urls[i][1]}</blockquote>"
            html_msg += "</details></p>"

        body += (
            "> \n"
            "> **Results from IMDb**"
        )
        html_msg += (
            "<p><b><sub>Results from IMDb</sub></b></p>"
            "</blockquote>"
        )

        return TextMessageEventContent(
            msgtype=MessageType.NOTICE,
            format=Format.HTML,
            body=body,
            formatted_body=html_msg)

    def get_max_results(self) -> int:
        """
        Get maximum number of results to return.
        :return: maximum number of results
        """
        try:
            max_results = int(self.config.get("max_results", 4))
            max_results = max(1, max_results)
        except ValueError:
            self.log.error("Incorrect 'max_results' config value. Setting default value of 4.")
            max_results = 4
        return max_results

    async def get_resized_image_url(self, url: str) -> str:
        """
        Append magic sequence of parameters to image URL in order to request resized image.
        :param url: image URL
        :return: altered image URL
        """
        img_base, quality_size, img_extension = url.rsplit(".", 2)
        if quality_size.startswith("_V1_"):
            # Magic sequence of params that IMDb appends to images
            # QL - JPEG quality
            # 300,444 - width, height
            quality_size = "_V1_QL90_UX300_CR0,0,300,444_"
            url = f"{img_base}.{quality_size}.{img_extension}"
        return url

    async def get_page_text(self, url: str) -> str:
        """
        Get page text from URL.
        :param url: page URL
        :return: page text
        """
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            response = await self.http.get(url, headers=self.headers, timeout=timeout, raise_for_status=True)
            text = await response.text()
        except aiohttp.ClientError as e:
            self.log.error(f"Scraping IMDb: Connection failed: {e}")
            return ""
        return text

    async def get_matrix_image_url(self, url: str) -> str:
        """
        Download image from external URL and upload it to Matrix
        :param url: external URL
        :return: matrix mxc URL
        """
        image_url = ""
        try:
            response = await self.http.get(url, raise_for_status=True)
            data = await response.read()
            content_type = response.content_type
            extension = mimetypes.guess_extension(content_type)
            image_url = await self.client.upload_media(
                data=data,
                mime_type=content_type,
                filename=f"image{extension}",
                size=len(data))
        except aiohttp.ClientError as e:
            self.log.error(f"Preparing image - connection failed: {url}: {e}")
        except Exception as e:
            self.log.error(f"Preparing image - unknown error: {url}: {e}")
        return image_url

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config
