from datetime import datetime,timezone
from enum import Enum
from pydantic import BaseModel,Field,HttpUrl

class Verdict(str,Enum):
    TRUE="TRUE"; MOSTLY_TRUE="MOSTLY TRUE"; PARTLY_TRUE="PARTLY TRUE"; MISLEADING="MISLEADING"
    MOSTLY_FALSE="MOSTLY FALSE"; FALSE="FALSE"; UNVERIFIED="UNVERIFIED"; OPINION="OPINION"; PREDICTION="PREDICTION"
class ContentType(str,Enum):
    AUTO="AUTO"; FACT="FACTUAL CLAIM"; ARGUMENT="ARGUMENT"; OPINION="OPINION"; PROPAGANDA="PROPAGANDA"
    PREDICTION="PREDICTION"; QUESTION="QUESTION"; SATIRE="SATIRE/UNCLEAR"; MIXED="MIXED"
class Source(BaseModel):
    title:str; publisher:str=""; url:HttpUrl; date:str=""; source_type:str="Other"; relevance:str=""; excerpt:str=""; retrieved_at:str=""
class Evidence(BaseModel):
    claim:str; excerpt:str; url:HttpUrl; title:str=""; publisher:str=""; source_type:str="Other"
class ClaimAssessment(BaseModel):
    claim:str; verdict:Verdict; confidence:int=Field(ge=0,le=100); summary:str
class FactCheckRequest(BaseModel):
    text:str=Field(min_length=1,max_length=12000)
    content_mode:str=Field(default="auto",pattern="^(auto|factual|argument|opinion|propaganda|prediction|question|satire|mixed)$")
    detail:str=Field(default="standard",pattern="^(quick|standard|detailed)$")
    audience:str=Field(default="general",pattern="^(general|student|professional|research)$")
    source_preference:str=Field(default="any",pattern="^(any|official|academic|news|factcheck)$")
    region:str=Field(default="global",max_length=80); language:str=Field(default="English",max_length=30)
class URLFactCheckRequest(FactCheckRequest): url:HttpUrl
class MediaAttachment(BaseModel):
    filename:str; media_type:str; kind:str; size_bytes:int; extracted_text:str=""; visual_summary:str=""
class FactCheckResult(BaseModel):
    claim:str; verdict:Verdict; confidence:int=Field(ge=0,le=100); summary:str; reasoning:str
    key_points:list[str]=[]; supporting_evidence:list[Evidence]=[]; contradicting_evidence:list[Evidence]=[]
    context:str=""; sources:list[Source]=[]; uncertainties:list[str]=[]; content_type:ContentType=ContentType.FACT
    report_title:str="Fact Check Analysis Report"; report_sections:list[str]=[]; attachments:list[MediaAttachment]=[]
    last_checked:str=Field(default_factory=lambda:datetime.now(timezone.utc).isoformat()); live_evidence_available:bool=False
    claims_checked:list[ClaimAssessment]=[]
class ArticleClaim(BaseModel):
    claim:str; verdict:Verdict; confidence:int=Field(ge=0,le=100); summary:str
class ArticleFactCheck(BaseModel):
    article_title:str=""; article_url:HttpUrl; overall_verdict:Verdict; overall_confidence:int=Field(ge=0,le=100)
    summary:str; claims_checked:list[ArticleClaim]=[]; sources:list[Source]=[]; uncertainties:list[str]=[]
    last_checked:str=Field(default_factory=lambda:datetime.now(timezone.utc).isoformat()); live_evidence_available:bool=False
