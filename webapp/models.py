from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CATEGORIES = {
    "sponsor": ("赞助广告", "视频中付费推广的产品或服务"),
    "selfpromo": ("自我推广", "创作者自己的商品、服务或频道推广"),
    "intro": ("片头", "重复的开场动画或介绍"),
    "outro": ("片尾", "结束语、鸣谢和片尾画面"),
    "music_offtopic": ("音乐间隙", "音乐视频里的非音乐部分"),
    "interaction": ("互动提醒", "点赞、订阅、评论或分享提醒"),
    "exclusive_access": ("会员推广", "付费订阅或会员专属内容推广"),
    "preview": ("预告与回顾", "即将播出内容或前情回顾"),
    "filler": ("离题内容", "与视频主题无关的填充内容"),
    "hook": ("开场引子", "正式内容前的问候或吸引注意片段"),
}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Device(Model):
    screen_id: str = Field(min_length=1, max_length=4096, pattern=r"^[^\s\x00-\x1f]+$")
    name: str = Field(default="YouTube on TV", min_length=1, max_length=100)
    offset: int = Field(default=0, ge=-60000, le=60000)


class Channel(Model):
    id: str = Field(pattern=r"^UC[A-Za-z0-9_-]{22}$")
    name: str = Field(min_length=1, max_length=200)


class BackendConfig(Model):
    devices: list[Device] = Field(default_factory=list, max_length=100)
    skip_categories: list[str] = Field(default_factory=lambda: ["sponsor"], max_length=10)
    skip_count_tracking: bool = True
    mute_ads: bool = True
    skip_ads: bool = True
    minimum_skip_length: float = Field(default=1, ge=0, le=3600)
    auto_play: bool = True
    join_name: str = Field(default="iSponsorBlockTV", min_length=1, max_length=100)
    apikey: str = Field(default="", max_length=512)
    channel_whitelist: list[Channel] = Field(default_factory=list, max_length=500)
    use_proxy: bool = False
    sponsorblock_api_url: str = Field(default="https://sponsor.ajay.app/api/", max_length=2048)

    @field_validator("skip_categories")
    @classmethod
    def categories(cls, value):
        if set(value) - CATEGORIES.keys():
            raise ValueError("包含不支持的跳过分类")
        return list(dict.fromkeys(value))

    @field_validator("sponsorblock_api_url")
    @classmethod
    def api_url(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("请输入不含凭据、查询参数和片段的 HTTP(S) 地址")
        return value.rstrip("/") + "/"

    @model_validator(mode="after")
    def coherent(self):
        if self.channel_whitelist and not self.apikey:
            raise ValueError("使用频道白名单前，请先填写 YouTube Data API 密钥")
        if len({d.screen_id for d in self.devices}) != len(self.devices):
            raise ValueError("同一台电视不能重复添加")
        if len({c.id for c in self.channel_whitelist}) != len(self.channel_whitelist):
            raise ValueError("白名单频道不能重复添加")
        return self


class ConfigUpdate(Model):
    revision: str
    config: BackendConfig


class Password(BaseModel):
    # Never trim a password: spaces may be intentional.
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=10, max_length=128)


class PasswordChange(Password):
    current_password: str = Field(max_length=128)


class Login(BaseModel):
    password: str = Field(max_length=128)


class Pair(Model):
    code: str = Field(max_length=32)
    name: str = Field(default="", max_length=100)
    revision: str

    @field_validator("code")
    @classmethod
    def pairing_code(cls, value):
        value = value.replace(" ", "").replace("-", "")
        if len(value) != 12 or not value.isascii() or not value.isdecimal():
            raise ValueError("电视配对码应为 12 位数字")
        return value


class Discovery(Model):
    active: bool = False


class ServiceAction(Model):
    action: Literal["start", "stop", "restart"]


class PanelSettings(Model):
    debug: bool = False
    http_tracing: bool = False
    proxy_url: str = Field(default="", max_length=2048)

    @field_validator("proxy_url")
    @classmethod
    def proxy(cls, value):
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("代理地址需要以 http:// 或 https:// 开头")
        return value
