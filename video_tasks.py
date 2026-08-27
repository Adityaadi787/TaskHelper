"""YouTube workflow helpers driven entirely by parsed task requirements."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

from playwright.async_api import Page

from agent import BrowserAgent, Candidate, score_candidate
from browser_manager import BrowserManager
from extractor import PageContent, ExtractionError, normalize_whitespace
from task_detector import VideoHints

logger=logging.getLogger("taskhelper.video_tasks")
YOUTUBE_SEARCH_URL="https://www.youtube.com/results?search_query={query}"

class VideoTaskError(Exception): pass

@dataclass
class VideoResult:
    url:str
    title:str
    channel:str|None
    info:str|None=None

def _seconds(value:str)->int:
    parts=[int(x) for x in value.split(":")]
    if len(parts)==2:return parts[0]*60+parts[1]
    if len(parts)==3:return parts[0]*3600+parts[1]*60+parts[2]
    raise ValueError(f"Invalid timestamp: {value}")

class VideoTaskHandler:
    def __init__(self,manager:BrowserManager,agent:BrowserAgent):self.manager=manager;self.agent=agent

    async def search_and_identify(self,page:Page,query:str,hints:VideoHints)->VideoResult:
        await self.manager.goto_with_retry(page,YOUTUBE_SEARCH_URL.format(query=quote_plus(query)))
        candidates=await self._collect_video_candidates(page,query,hints)
        if not candidates:raise VideoTaskError(f"No YouTube video results found for query {query!r}.")
        best=candidates[0]; await self.agent.open_candidate(page,best)
        content=await self.agent.get_content(page)
        return VideoResult(page.url,self._extract_title(content) or best.text,self._extract_channel(content))

    async def _collect_video_candidates(self,page:Page,query:str,hints:VideoHints)->list[Candidate]:
        items=await page.eval_on_selector_all("a[href*='/watch']", """els => els.map(e => ({href:e.href,text:(e.innerText||'').trim(),
          img:Array.from(e.querySelectorAll('img')).map(i=>`${i.alt||''} ${i.title||''}`).join(' ')}))""")
        out=[];seen=set()
        for item in items:
            href=item.get("href") or ""; text=(item.get("text") or "").strip(); img=(item.get("img") or "").strip()
            if not href or "/watch" not in href or href in seen or not text:continue
            seen.add(href); score=score_candidate(text+" "+img,href,query,None)
            if hints.title_hint and hints.title_hint.lower() in text.lower():score+=3
            if hints.channel_hint and hints.channel_hint.lower() in text.lower():score+=1
            if hints.thumbnail_hint and hints.thumbnail_hint.lower() in img.lower():score+=2
            out.append(Candidate(href,text,score))
        out.sort(key=lambda c:c.score,reverse=True); return out

    def _extract_title(self,content:PageContent)->str|None:
        soup=content.soup()
        for selector in ("h1","meta[name='title']","meta[property='og:title']","title"):
            tag=soup.select_one(selector)
            if tag:
                value=tag.get("content") if tag.name=="meta" else tag.get_text(" ",strip=True)
                if value:return normalize_whitespace(value)
        return None

    def _extract_channel(self,content:PageContent)->str|None:
        soup=content.soup()
        for selector in ("link[itemprop='name']","span[itemprop='author'] link[itemprop='name']", "meta[itemprop='author']"):
            tag=soup.select_one(selector)
            if tag and (tag.get("content") or tag.get("href")):return tag.get("content") or tag.get("href")
        return None

    async def perform_timing_requirement(self,page:Page,hints:VideoHints)->str|None:
        """Seek/watch according to parsed timing requirements and return captions when available."""
        if not (hints.timestamp or hints.interval_start or hints.duration_seconds):return None
        target=hints.timestamp or hints.interval_start
        if target:
            seconds=_seconds(target)
            try:
                await page.eval_on_selector("video", "(v,s) => { v.currentTime=s; v.play().catch(()=>{}); }", seconds)
            except Exception as exc:
                raise VideoTaskError("YouTube video element was not accessible for timestamp seeking") from exc
            await asyncio.sleep(0.8)
        if hints.interval_start and hints.interval_end:
            wait=max(0,_seconds(hints.interval_end)-_seconds(hints.interval_start))
            if wait: await asyncio.sleep(wait)
        elif hints.duration_seconds:
            await asyncio.sleep(hints.duration_seconds)
        captions=await self._read_captions(page)
        if hints.reference_text and captions:
            for line in captions.splitlines():
                if hints.reference_text.lower() in line.lower():return line
            raise VideoTaskError("Requested reference text was not present in available captions")
        return captions or None

    async def _read_captions(self,page:Page)->str|None:
        selectors=[".ytp-caption-segment", ".caption-window .caption-visual-line", "[class*='caption']"]
        for selector in selectors:
            try:
                values=await page.locator(selector).all_inner_texts()
                text="\n".join(normalize_whitespace(v) for v in values if v.strip())
                if text:return text
            except Exception:continue
        return None

    async def extract_requested_info(self,content:PageContent,hints:VideoHints,page:Page|None=None)->str:
        soup=content.soup(); text=content.text()
        if hints.timestamp and page is not None:
            timed=await self.perform_timing_requirement(page,hints)
            if timed:return timed
        if hints.info_request=="view_count":
            m=re.search(r"([\d,\.]+\s*[KMB]?)\s+views?",text,re.I)
            if m:return m.group(1).replace(" ","")
            meta=soup.find("meta",itemprop="interactionCount")
            if meta and meta.get("content"):return meta["content"]
        elif hints.info_request=="upload_date":
            meta=soup.find("meta",itemprop="uploadDate") or soup.find("meta",itemprop="datePublished")
            if meta and meta.get("content"):return meta["content"]
        elif hints.info_request=="description":
            meta=soup.find("meta",attrs={"name":"description"}) or soup.find("meta",attrs={"property":"og:description"})
            if meta and meta.get("content"):return meta["content"]
        elif hints.info_request in ("title","channel_name"):
            value=self._extract_title(content) if hints.info_request=="title" else self._extract_channel(content)
            if value:return value
        elif hints.info_request=="like_count":
            m=re.search(r"([\d,\.]+\s*[KMB]?)\s+likes?",text,re.I)
            if m:return m.group(1).replace(" ","")
        elif hints.info_request=="subscriber_count":
            m=re.search(r"([\d,\.]+\s*[KMB]?)\s+subscribers?",text,re.I)
            if m:return m.group(1).replace(" ","")
        if hints.reference_text:
            m=re.search(re.escape(hints.reference_text)+r"[^\n]{0,300}",text,re.I)
            if m:return normalize_whitespace(m.group(0))
        if hints.timestamp:
            raise VideoTaskError("Timestamp was requested, but no accessible captions/content were available at that point.")
        raise VideoTaskError(f"Requested YouTube information was not found: {hints.info_request or 'unspecified'}")
