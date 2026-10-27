"""
Anna University -- Automated Academic PDF Generator
Dhanalakshmi College of Engineering, Chennai
Regulation: R-2021 | API: Groq openai/gpt-oss-120b (free tier, no card)
Usage: python generate_all_pdfs.py

2026-09-25 (BL-015 follow-up): uses the shared App modules
  - pdf_render : embedded DejaVu Unicode fonts + text normalisation (no more
                 deleting non-ASCII characters or every "?")
  - qa_format  : the official exam pattern per regulation (R-2021: 10x2, 5x13, 1x15)
Groq JSON mode, truncated answers refused, question counts checked, honest summary.
"""

import os, sys, json, time, re
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape, unescape as _xml_unescape
import requests

# Shared modules live in AcademicsGen\App (this file also has a copy in the
# scripts folder, so look next to this file first, then in the sibling App folder).
_HERE = Path(__file__).resolve().parent
for _cand in (_HERE, _HERE.parent / "AcademicsGen" / "App"):
    if (_cand / "pdf_render.py").is_file():
        sys.path.insert(0, str(_cand))
        break
import pdf_render
from qa_format import get_pattern
pdf_render.register_fonts()

class _GroqClient:
    """Tiny stand-in for the old Cerebras SDK client: same call shape
    (client.chat.completions.create(...)), served by Groq's free tier (no card)."""
    URL = "https://api.groq.com/openai/v1/chat/completions"
    def __init__(self, api_key):
        self.api_key = api_key
        self.chat = self
        self.completions = self
    def create(self, model, messages, max_tokens, temperature):
        r = requests.post(self.URL, timeout=180,
                          headers={"Authorization": f"Bearer {self.api_key}"},
                          json={"model": model, "messages": messages,
                                "max_completion_tokens": max_tokens, "temperature": temperature,
                                "reasoning_effort": "low",
                                "response_format": {"type": "json_object"}})   # Groq JSON mode
        if r.status_code != 200:
            raise RuntimeError(f"Groq HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        choice = j["choices"][0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("answer was cut off (hit the token limit)")
        msg = type("M", (), {"content": choice["message"].get("content") or ""})
        return type("R", (), {"choices": [type("C", (), {"message": msg})]})
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch, cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, Flowable, HRFlowable, Preformatted, KeepTogether
)

# CONFIG
COLLEGE     = "Dhanalakshmi College of Engineering"
REGULATION  = "R-2021"
DEPTS       = "CSE | IT | AI&DS | AI&ML | Cyber Security"
OUTPUT_BASE = r"C:\Users\johnb\Documents\Teaching"
MODEL       = "openai/gpt-oss-120b"
UNITS_INFO  = [("I",9),("II",5),("III",13),("IV",12),("V",6)]
_PAT        = {p.key: p for p in get_pattern(REGULATION)}   # official pattern for REGULATION
PART_A, PART_B, PART_C = _PAT["part_a"], _PAT["part_b"], _PAT["part_c"]

# COLORS
NAVY=colors.HexColor("#0D1B4B"); GOLD=colors.HexColor("#C9A84C")
WHITE=colors.white; BLACK=colors.black
MID_GREY=colors.HexColor("#888888"); DOT_GREY=colors.HexColor("#AAAAAA")
LIGHT_GREY=colors.HexColor("#F5F5F5"); LIGHT_BLUE=colors.HexColor("#E8EDF7")
STEEL_BLUE=colors.HexColor("#BBDEFB"); CODE_BG=colors.HexColor("#F4F4F4")
DARK_RED=colors.HexColor("#8B0000"); ORANGE=colors.HexColor("#CC5500")
DARK_GREEN=colors.HexColor("#1A6B1A"); GREEN_BG=colors.HexColor("#90EE90")
DARK_GREEN_BG=colors.HexColor("#1A6B1A"); YELLOW_BG=colors.HexColor("#FFFACD")
ALGO_BG=colors.HexColor("#FFFDE7"); WORKED_BG=colors.HexColor("#F1F8E9")
PAGE_W,PAGE_H=A4; MARGIN=inch; CONTENT_W=PAGE_W-2*MARGIN


# FLOWABLES
class NavyBar(Flowable):
    def __init__(self,text,width=CONTENT_W,height=26,fontsize=11):
        Flowable.__init__(self); self.text=text; self.bar_width=width; self.height=height; self.fontsize=fontsize
    def draw(self):
        self.canv.setFillColor(NAVY); self.canv.rect(0,0,self.bar_width,self.height,fill=1,stroke=0)
        self.canv.setFillColor(WHITE); self.canv.setFont("AG-Serif-Bold",self.fontsize); self.canv.drawString(10,7,_xml_unescape(self.text))

class DottedLine(Flowable):
    def __init__(self,width=CONTENT_W):
        Flowable.__init__(self); self.line_width=width; self.height=10
    def draw(self):
        self.canv.setStrokeColor(DOT_GREY); self.canv.setDash(2,4); self.canv.setLineWidth(0.5)
        self.canv.line(0,5,self.line_width,5); self.canv.setDash()

class DoubleLine(Flowable):
    def __init__(self,width=CONTENT_W):
        Flowable.__init__(self); self.line_width=width; self.height=14
    def draw(self):
        self.canv.setStrokeColor(NAVY); self.canv.setLineWidth(1.5)
        self.canv.line(0,10,self.line_width,10); self.canv.line(0,6,self.line_width,6)


# STYLES
def get_styles():
    s={}
    s["body"]         = ParagraphStyle("body",         fontName="AG-Serif",  fontSize=11,leading=17,textColor=BLACK,   alignment=TA_JUSTIFY,spaceAfter=5)
    s["answer"]       = ParagraphStyle("answer",       fontName="AG-Serif",  fontSize=11,leading=16,textColor=BLACK,   alignment=TA_JUSTIFY)
    s["unit_head"]    = ParagraphStyle("uhead",        fontName="AG-Serif-Bold",   fontSize=16,leading=22,textColor=NAVY,    alignment=TA_CENTER,spaceBefore=10,spaceAfter=6)
    s["topic"]        = ParagraphStyle("topic",        fontName="AG-Serif-Bold",   fontSize=14,leading=20,textColor=NAVY,    spaceBefore=12,spaceAfter=4)
    s["subtopic"]     = ParagraphStyle("subtopic",     fontName="AG-Serif-Bold",   fontSize=12,leading=18,textColor=NAVY,    spaceBefore=8,spaceAfter=3)
    s["subhead"]      = ParagraphStyle("subhead",      fontName="AG-Serif-Bold",   fontSize=10,leading=14,textColor=NAVY,    spaceBefore=6,spaceAfter=2)
    s["label"]        = ParagraphStyle("label",        fontName="AG-Serif-Bold",   fontSize=10,leading=14,textColor=NAVY,    spaceBefore=4,spaceAfter=2)
    s["caption"]      = ParagraphStyle("caption",      fontName="AG-Serif-Italic", fontSize=9, leading=12,textColor=MID_GREY,alignment=TA_CENTER,spaceAfter=4)
    s["bullet"]       = ParagraphStyle("bullet",       fontName="AG-Serif",  fontSize=11,leading=16,leftIndent=20,     spaceAfter=3)
    s["code"]         = ParagraphStyle("code",         fontName="AG-Mono",      fontSize=9, leading=13,textColor=BLACK,   leftIndent=5)
    s["ptr"]          = ParagraphStyle("ptr",          fontName="AG-Serif",  fontSize=10,leading=15,textColor=BLACK,   leftIndent=10,spaceAfter=2)
    s["ptr_head"]     = ParagraphStyle("phead",        fontName="AG-Serif-Bold",   fontSize=10,leading=14,textColor=NAVY,    spaceAfter=3)
    s["center"]       = ParagraphStyle("center",       fontName="AG-Serif",  fontSize=11,leading=16,alignment=TA_CENTER)
    s["qnum"]         = ParagraphStyle("qnum",         fontName="AG-Serif-Bold",   fontSize=11,leading=16,textColor=BLACK,   spaceBefore=4)
    s["answer_label"] = ParagraphStyle("answer_label", fontName="AG-Serif-Bold",   fontSize=11,leading=14,textColor=BLACK,   spaceBefore=2)
    s["legend_title"] = ParagraphStyle("legend_title", fontName="AG-Serif-Bold",   fontSize=10,leading=14,textColor=NAVY)
    s["legend_item"]  = ParagraphStyle("legend_item",  fontName="AG-Serif",  fontSize=10,leading=14,textColor=BLACK)
    s["title_main"]   = ParagraphStyle("tmain",        fontName="AG-Serif-Bold",   fontSize=20,leading=26,textColor=WHITE,   alignment=TA_CENTER)
    s["title_sub"]    = ParagraphStyle("tsub",         fontName="AG-Serif-Bold",   fontSize=14,leading=20,textColor=GOLD,    alignment=TA_CENTER)
    s["title_info"]   = ParagraphStyle("tinfo",        fontName="AG-Serif",  fontSize=12,leading=18,textColor=WHITE,   alignment=TA_CENTER)
    s["syllabus"]     = ParagraphStyle("syllabus",     fontName="AG-Serif",  fontSize=10,leading=15,textColor=BLACK,   alignment=TA_JUSTIFY)
    s["beyond_topic"] = ParagraphStyle("beyond_topic", fontName="AG-Serif-Bold",   fontSize=10,leading=14,textColor=NAVY)
    s["beyond_exp"]   = ParagraphStyle("beyond_exp",   fontName="AG-Serif",  fontSize=10,leading=14,textColor=BLACK,   alignment=TA_JUSTIFY)
    s["high_pri"]     = ParagraphStyle("high_pri",     fontName="AG-Serif-Bold",   fontSize=9, leading=13,textColor=DARK_RED)
    s["med_pri"]      = ParagraphStyle("med_pri",      fontName="AG-Serif-Bold",   fontSize=9, leading=13,textColor=ORANGE)
    s["low_pri"]      = ParagraphStyle("low_pri",      fontName="AG-Serif-Bold",   fontSize=9, leading=13,textColor=DARK_GREEN)
    s["focus_topic"]  = ParagraphStyle("focus_topic",  fontName="AG-Serif-Bold",   fontSize=9, leading=13,textColor=BLACK)
    s["focus_note"]   = ParagraphStyle("focus_note",   fontName="AG-Serif",  fontSize=9, leading=13,textColor=BLACK)
    s["exam_q"]       = ParagraphStyle("examq",        fontName="AG-Serif",  fontSize=10,leading=15,textColor=BLACK,   leftIndent=10,spaceAfter=2)
    s["section_head"] = ParagraphStyle("section_head", fontName="AG-Serif-Bold",   fontSize=11,leading=16,textColor=NAVY,    spaceBefore=8,spaceAfter=4)
    s["vn_body"]      = ParagraphStyle("vn_body",      fontName="AG-Serif",  fontSize=11,leading=17,textColor=BLACK,   alignment=TA_JUSTIFY,spaceAfter=4)
    return s


# HELPER BOXES
def defn_box(text,styles):
    data=[[Paragraph("<b>DEFINITION</b>",styles["label"])],[Paragraph(text,styles["body"])]]
    t=Table(data,colWidths=[CONTENT_W])
    t.setStyle(TableStyle([("BOX",(0,0),(-1,-1),1.5,NAVY),("BACKGROUND",(0,0),(-1,0),LIGHT_BLUE),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8)]))
    return t

def ptr_box(points,styles,master=False):
    bg=STEEL_BLUE if master else LIGHT_BLUE
    heading="MASTER POINTS TO REMEMBER" if master else "POINTS TO REMEMBER"
    rows=[[Paragraph(f"<b>{heading}</b>",styles["ptr_head"])]]
    for pt in points: rows.append([Paragraph(f"->  {pt}",styles["ptr"])])
    t=Table(rows,colWidths=[CONTENT_W])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),bg),("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8)]))
    return t

def exam_focus_box(text,styles):
    data=[[Paragraph("<b>EXAM FOCUS</b>",styles["label"])],[Paragraph(text,styles["body"])]]
    t=Table(data,colWidths=[CONTENT_W])
    t.setStyle(TableStyle([("BOX",(0,0),(-1,-1),1.5,NAVY),("BACKGROUND",(0,0),(-1,0),LIGHT_BLUE),
        ("BACKGROUND",(0,1),(-1,-1),LIGHT_GREY),("TOPPADDING",(0,0),(-1,-1),5),
        ("BOTTOMPADDING",(0,0),(-1,-1),5),("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8)]))
    return t

def tips_box(text,styles):
    data=[[Paragraph("<b>TIP</b>",styles["label"])],[Paragraph(text,styles["body"])]]
    t=Table(data,colWidths=[CONTENT_W])
    t.setStyle(TableStyle([("BOX",(0,0),(-1,-1),1.5,NAVY),("BACKGROUND",(0,0),(-1,0),STEEL_BLUE),
        ("BACKGROUND",(0,1),(-1,-1),LIGHT_GREY),("TOPPADDING",(0,0),(-1,-1),5),
        ("BOTTOMPADDING",(0,0),(-1,-1),5),("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8)]))
    return t

def key_points_box(points,styles):
    rows=[[Paragraph("<b>KEY POINTS TO REMEMBER</b>",styles["ptr_head"])]]
    for pt in points: rows.append([Paragraph("*  "+pt,styles["ptr"])])
    t=Table(rows,colWidths=[CONTENT_W])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),STEEL_BLUE),("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8)]))
    return t

def code_box(text,styles):
    t=Table([[Preformatted(_xml_unescape(text),styles["code"])]],colWidths=[CONTENT_W])
    t.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.5,MID_GREY),("BACKGROUND",(0,0),(-1,-1),CODE_BG),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8)]))
    return t

def algo_box(name,steps,styles):
    as_=ParagraphStyle("algo",fontName="AG-Mono",fontSize=9,leading=13,leftIndent=12,textColor=BLACK,wordWrap="CJK")
    at_=ParagraphStyle("algo_title",fontName="AG-Mono",fontSize=9,leading=13,textColor=BLACK,spaceAfter=4)
    body_steps=[]; input_line=""; output_line=""
    for s in steps:
        sl=s.strip()
        if sl.lower().startswith("input:"): input_line=sl
        elif sl.lower().startswith("output:"): output_line=sl
        else: body_steps.append(sl)
    rows=[]; rows.append([Paragraph(f"<b>Algorithm: {name}</b>",at_)]); rows.append([Spacer(1,4)])
    if input_line: rows.append([Paragraph(input_line,as_)])
    if output_line: rows.append([Paragraph(output_line,as_)])
    if input_line or output_line: rows.append([Spacer(1,4)])
    for i,s in enumerate(body_steps,1):
        line=s if s.lower().startswith("step") else f"Step {i}: {s}"
        rows.append([Paragraph(line,as_)])
    rows.append([Paragraph("Step End",as_)])
    inner_w=CONTENT_W-16
    t=Table([[row[0]] for row in rows],colWidths=[inner_w])
    t.setStyle(TableStyle([("BOX",(0,0),(-1,-1),1.0,MID_GREY),("BACKGROUND",(0,0),(-1,-1),ALGO_BG),
        ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8)]))
    return t

def worked_example_box(title,text,styles):
    ls=ParagraphStyle("we_label",fontName="AG-Serif-Bold",fontSize=10,leading=14,textColor=BLACK,spaceAfter=4)
    bs=ParagraphStyle("we_body",fontName="AG-Serif",fontSize=10,leading=14,textColor=BLACK,wordWrap="LTR")
    inner_w=CONTENT_W-16
    t=Table([[Paragraph(f"<b>Example: {title}</b>",ls)],[Paragraph(text,bs)]],colWidths=[inner_w])
    t.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.5,MID_GREY),("BACKGROUND",(0,0),(-1,-1),WORKED_BG),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8)]))
    return t

def make_table(headers,rows,header_bg=LIGHT_BLUE,col_widths=None):
    all_rows=[[Paragraph(f"<b>{h}</b>",ParagraphStyle("th",fontName="AG-Serif-Bold",fontSize=10,leading=14,textColor=BLACK)) for h in headers]]
    for row in rows:
        all_rows.append([Paragraph(str(c),ParagraphStyle("td",fontName="AG-Serif",fontSize=10,leading=14,textColor=BLACK)) for c in row])
    if col_widths is None: col_widths=[CONTENT_W/len(headers)]*len(headers)
    t=Table(all_rows,colWidths=col_widths)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),header_bg),("FONTNAME",(0,0),(-1,0),"AG-Serif-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),10),("BOX",(0,0),(-1,-1),0.5,MID_GREY),("INNERGRID",(0,0),(-1,-1),0.3,MID_GREY),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE,LIGHT_GREY])]))
    return t

def feature_table(headers,rows_data):
    hc=[Paragraph("<b>"+h+"</b>",ParagraphStyle("fth",fontName="AG-Serif-Bold",fontSize=10,textColor=WHITE,leading=14)) for h in headers]
    all_rows=[hc]
    for row in rows_data:
        all_rows.append([Paragraph(str(c),ParagraphStyle("ftd",fontName="AG-Serif",fontSize=10,leading=14,textColor=BLACK)) for c in row])
    col_w=[CONTENT_W/len(headers)]*len(headers)
    t=Table(all_rows,colWidths=col_w)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),NAVY),("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE,LIGHT_GREY]),
        ("BOX",(0,0),(-1,-1),0.5,MID_GREY),("INNERGRID",(0,0),(-1,-1),0.3,MID_GREY),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),("VALIGN",(0,0),(-1,-1),"TOP")]))
    return t

def adv_disadv_table(advantages,disadvantages):
    header=[Paragraph("<b>Advantages</b>",ParagraphStyle("adh",fontName="AG-Serif-Bold",fontSize=10,textColor=WHITE,leading=14)),
            Paragraph("<b>Disadvantages</b>",ParagraphStyle("ddh",fontName="AG-Serif-Bold",fontSize=10,textColor=WHITE,leading=14))]
    rows=[header]
    for i in range(max(len(advantages),len(disadvantages))):
        a="+ "+advantages[i] if i<len(advantages) else ""
        d="- "+disadvantages[i] if i<len(disadvantages) else ""
        rows.append([Paragraph(a,ParagraphStyle("ad",fontName="AG-Serif",fontSize=10,leading=14,textColor=BLACK)),
                     Paragraph(d,ParagraphStyle("dd",fontName="AG-Serif",fontSize=10,leading=14,textColor=BLACK))])
    hw=CONTENT_W/2
    t=Table(rows,colWidths=[hw,hw])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(0,0),NAVY),("BACKGROUND",(1,0),(1,0),NAVY),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE,LIGHT_GREY]),("BOX",(0,0),(-1,-1),0.5,MID_GREY),
        ("INNERGRID",(0,0),(-1,-1),0.3,MID_GREY),("TOPPADDING",(0,0),(-1,-1),5),
        ("BOTTOMPADDING",(0,0),(-1,-1),5),("LEFTPADDING",(0,0),(-1,-1),6),
        ("RIGHTPADDING",(0,0),(-1,-1),6),("VALIGN",(0,0),(-1,-1),"TOP")]))
    return t

def legend_box(styles):
    data=[[Paragraph("<b>IMPORTANCE LEGEND</b>",styles["legend_title"])],
          [Paragraph("*** -- Very important (AI estimate)",styles["legend_item"])],
          [Paragraph("**  -- Important (AI estimate)",styles["legend_item"])],
          [Paragraph("*    -- Good to know (AI estimate)",styles["legend_item"])],
          [Paragraph("<i>Stars are the model's estimate, not counted from real past papers.</i>",styles["legend_item"])]]
    t=Table(data,colWidths=[CONTENT_W-20])
    t.setStyle(TableStyle([("BOX",(0,0),(-1,-1),1,NAVY),("BACKGROUND",(0,0),(-1,0),LIGHT_BLUE),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
        ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10)]))
    return t

def q_header_table(q_text,marks,star,styles):
    left=Paragraph(q_text,styles["qnum"])
    right=Paragraph(f"({marks} Marks) {star}",ParagraphStyle("r",fontName="AG-Serif-Bold",fontSize=10,alignment=TA_RIGHT,textColor=NAVY))
    t=Table([[left,right]],colWidths=[CONTENT_W*0.78,CONTENT_W*0.22])
    t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),
        ("RIGHTPADDING",(0,0),(-1,-1),0),("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    return t


# TITLE PAGES
def title_page_notes(subject_code,subject_name,unit_num,unit_title,subject_type,ltpc,styles):
    story=[]
    edition="CSE Core Edition" if subject_type=="core" else "Coding Edition"
    td=[
        [Paragraph(COLLEGE,ParagraphStyle("cp",fontName="AG-Serif-Bold",fontSize=13,textColor=GOLD,alignment=TA_CENTER))],
        [Paragraph(f"Anna University, Chennai -- {REGULATION}",ParagraphStyle("cp2",fontName="AG-Serif",fontSize=11,textColor=WHITE,alignment=TA_CENTER))],
        [Spacer(1,16)],
        [Paragraph(f"{subject_code} -- {subject_name.upper()}",styles["title_main"])],
        [HRFlowable(width=CONTENT_W-40,color=GOLD,thickness=1)],
        [Spacer(1,10)],
        [Paragraph(f"Unit {unit_num} -- {unit_title}",styles["title_sub"])],
        [Spacer(1,10)],
        [Paragraph(f"L T P C -- {ltpc}",styles["title_info"])],
        [Spacer(1,8)],
        [Paragraph(f"Student Reference Notes -- {edition}",ParagraphStyle("te",fontName="AG-Serif-Italic",fontSize=13,textColor=WHITE,alignment=TA_CENTER))],
        [Spacer(1,20)],
        [Paragraph(f"Departments: {DEPTS}",styles["title_info"])],
        [Paragraph(f"Regulation: {REGULATION}",styles["title_info"])],
        [Paragraph("Anna University",styles["title_info"])],
    ]
    tt=Table(td,colWidths=[CONTENT_W])
    tt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),NAVY),("TOPPADDING",(0,0),(-1,-1),8),
        ("BOTTOMPADDING",(0,0),(-1,-1),8),("LEFTPADDING",(0,0),(-1,-1),20),
        ("RIGHTPADDING",(0,0),(-1,-1),20),("BOX",(0,0),(-1,-1),2,GOLD)]))
    story.append(Spacer(1,inch)); story.append(tt); story.append(PageBreak())
    return story

def title_page_qa(subject_code,subject_name,unit_num,unit_title,styles):
    story=[]
    td=[
        [Paragraph(COLLEGE,ParagraphStyle("cp",fontName="AG-Serif-Bold",fontSize=13,textColor=GOLD,alignment=TA_CENTER))],
        [Paragraph(f"Anna University, Chennai -- {REGULATION}",ParagraphStyle("cp2",fontName="AG-Serif",fontSize=11,textColor=WHITE,alignment=TA_CENTER))],
        [Spacer(1,16)],
        [Paragraph(f"{subject_code} -- {subject_name.upper()}",styles["title_main"])],
        [Spacer(1,10)],
        [Paragraph(f"Unit {unit_num} -- {unit_title}",styles["title_sub"])],
        [Spacer(1,16)],
        [Paragraph("Important Questions with Model Answers",ParagraphStyle("tl",fontName="AG-Serif-Italic",fontSize=13,textColor=WHITE,alignment=TA_CENTER))],
        [Paragraph(f"Part A ({PART_A.marks} Marks), Part B ({PART_B.marks} Marks) and Part C ({PART_C.marks} Marks)",ParagraphStyle("tl2",fontName="AG-Serif",fontSize=12,textColor=GOLD,alignment=TA_CENTER))],
        [Spacer(1,24)],
        [Paragraph(f"Departments: {DEPTS}",styles["title_info"])],
        [Paragraph(f"Regulation: {REGULATION}",styles["title_info"])],
    ]
    tt=Table(td,colWidths=[CONTENT_W])
    tt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),NAVY),("TOPPADDING",(0,0),(-1,-1),8),
        ("BOTTOMPADDING",(0,0),(-1,-1),8),("LEFTPADDING",(0,0),(-1,-1),20),
        ("RIGHTPADDING",(0,0),(-1,-1),20),("BOX",(0,0),(-1,-1),2,GOLD)]))
    story.append(Spacer(1,1.2*inch)); story.append(tt); story.append(Spacer(1,0.3*inch))
    info_rows=[["Subject Code",":",subject_code],["Subject",":",subject_name],
               ["Unit",":",f"{unit_num} -- {unit_title}"],["Regulation",":",REGULATION],["College",":",COLLEGE]]
    info_table=Table(info_rows,colWidths=[120,15,CONTENT_W-135])
    info_table.setStyle(TableStyle([("FONTNAME",(0,0),(-1,-1),"AG-Serif"),("FONTNAME",(0,0),(0,-1),"AG-Serif-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),11),("BOX",(0,0),(-1,-1),1,NAVY),("INNERGRID",(0,0),(-1,-1),0.25,MID_GREY),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),8),("BACKGROUND",(0,0),(0,-1),LIGHT_BLUE)]))
    story.append(info_table); story.append(PageBreak())
    return story

def title_page_beyond(subject_code,subject_name,styles):
    story=[]
    td=[
        [Paragraph(COLLEGE,ParagraphStyle("cp",fontName="AG-Serif-Bold",fontSize=13,textColor=GOLD,alignment=TA_CENTER))],
        [Paragraph(f"Anna University, Chennai -- {REGULATION}",ParagraphStyle("cp2",fontName="AG-Serif",fontSize=11,textColor=WHITE,alignment=TA_CENTER))],
        [Spacer(1,16)],
        [Paragraph(f"{subject_code} -- {subject_name.upper()}",styles["title_main"])],
        [Spacer(1,10)],
        [Paragraph("All 5 Units | Industry-Aligned | Placement-Relevant | 2025",styles["title_sub"])],
        [Spacer(1,16)],
        [Paragraph("Beyond the Syllabus & Exam Focus",ParagraphStyle("tl",fontName="AG-Serif-Italic",fontSize=13,textColor=WHITE,alignment=TA_CENTER))],
        [Paragraph("Faculty Reference Document",ParagraphStyle("tl2",fontName="AG-Serif",fontSize=12,textColor=GOLD,alignment=TA_CENTER))],
        [Spacer(1,24)],
        [Paragraph(f"Departments: {DEPTS}",styles["title_info"])],
        [Paragraph(f"Regulation: {REGULATION}",styles["title_info"])],
    ]
    tt=Table(td,colWidths=[CONTENT_W])
    tt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),NAVY),("TOPPADDING",(0,0),(-1,-1),8),
        ("BOTTOMPADDING",(0,0),(-1,-1),8),("LEFTPADDING",(0,0),(-1,-1),20),
        ("RIGHTPADDING",(0,0),(-1,-1),20),("BOX",(0,0),(-1,-1),2,GOLD)]))
    story.append(Spacer(1,1.2*inch)); story.append(tt); story.append(Spacer(1,0.3*inch))
    info_rows=[["Subject Code",":",subject_code],["Subject",":",subject_name],
               ["Content",":","Beyond Syllabus + Exam Focus -- All 5 Units"],["Regulation",":",REGULATION],["College",":",COLLEGE]]
    info_table=Table(info_rows,colWidths=[120,15,CONTENT_W-135])
    info_table.setStyle(TableStyle([("FONTNAME",(0,0),(-1,-1),"AG-Serif"),("FONTNAME",(0,0),(0,-1),"AG-Serif-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),11),("BOX",(0,0),(-1,-1),1,NAVY),("INNERGRID",(0,0),(-1,-1),0.25,MID_GREY),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),8),("BACKGROUND",(0,0),(0,-1),LIGHT_BLUE)]))
    story.append(info_table); story.append(PageBreak())
    return story


# FOOTERS
def make_footer_notes(subject_code,subject_name,unit_num):
    def _f(c,doc):
        c.saveState(); c.setFont("AG-Serif",8); c.setFillColor(MID_GREY)
        c.drawString(MARGIN,0.4*inch,f"{subject_code} {subject_name} -- Unit {unit_num} -- Student Notes | Page {doc.page}"); c.restoreState()
    return _f

def make_footer_qa(subject_code,subject_name,unit_num):
    def _f(c,doc):
        c.saveState(); c.setFont("AG-Serif",8); c.setFillColor(MID_GREY)
        c.drawString(MARGIN,0.45*inch,f"{subject_code} {subject_name} -- Unit {unit_num} -- Important Questions | Page {doc.page}"); c.restoreState()
    return _f

def make_footer_beyond(subject_code,subject_name):
    def _f(c,doc):
        c.saveState(); c.setFont("AG-Serif",8); c.setFillColor(MID_GREY)
        c.drawString(MARGIN,0.45*inch,f"{subject_code} {subject_name} -- Beyond the Syllabus -- Faculty Reference | Page {doc.page}"); c.restoreState()
    return _f

def make_footer_verified(subject_code,subject_name,unit_num):
    def _f(c,doc):
        c.saveState(); c.setFont("AG-Serif",8); c.setFillColor(MID_GREY)
        c.drawString(MARGIN,0.4*inch,f"{subject_code} {subject_name} -- Unit {unit_num} -- Verified Source Notes | Page {doc.page}"); c.restoreState()
    return _f


# API
def clean_text(text):
    """Model text -> safe Paragraph markup. Keeps every character the embedded
    fonts can draw (arrows, quotes, dashes, "?"...), maps invisible/look-alike
    characters, shows anything undrawable as U+FFFD, and escapes & < >.
    (The old version forced ASCII and deleted every question mark.)"""
    if not isinstance(text,str): return text
    return _xml_escape(pdf_render.normalize_text(text)).strip()

def clean_dict(obj):
    if isinstance(obj,str): return clean_text(obj)
    elif isinstance(obj,list): return [clean_dict(i) for i in obj]
    elif isinstance(obj,dict): return {k:clean_dict(v) for k,v in obj.items()}
    return obj

def call_json_expect(client,prompt,key,count,max_retries=3):
    """call_gemini + check that data[key] has exactly `count` items (re-ask if not)."""
    last=None
    for attempt in range(1,max_retries+1):
        data=call_gemini(client,prompt)
        items=data.get(key)
        if isinstance(items,list) and len(items)==count and all(isinstance(i,dict) for i in items):
            return data
        last=f"{key} has {len(items) if isinstance(items,list) else 'no'} items, expected exactly {count}"
        print(f"    Rejected: {last} (try {attempt}/{max_retries})")
    raise RuntimeError(f"Wrong question count after {max_retries} tries: {last}")

def call_gemini(client,prompt,max_retries=5):
    for attempt in range(1,max_retries+1):
        try:
            print(f"    Calling Groq API (attempt {attempt})...")
            response=client.chat.completions.create(
                model=MODEL,
                messages=[{"role":"user","content":prompt}],
                max_tokens=6000,temperature=0.5)
            raw=response.choices[0].message.content.strip()
            raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw).strip()   # JSON mode normally has no fences
            data=json.loads(raw)
            if not isinstance(data,dict): raise ValueError("response JSON is not an object")
            return clean_dict(data)
        except Exception as e:
            print(f"    Attempt {attempt} failed: {e}")
            if attempt<max_retries:
                wait=15*attempt; print(f"    Waiting {wait}s..."); time.sleep(wait)
    raise RuntimeError("Groq API failed after all retries.")

# PROMPTS
NOTES_TOPIC_PROMPT = """
Act as a professor at Anna University, Chennai.
You are an expert teaching {subject_code} - {subject_name} under Regulation R-2021.
Provide comprehensive study notes for low and average BE students to score high in exams.
UNDERSTAND the verified source context and use it as primary reference.
UNDERSTAND SUBSCRIPT, SUPERSCRIPT and FORMULA - represent clearly in plain text.
Cover Anna University question patterns - 2-mark, 13-mark and 15-mark questions.
Generate notes for ONE topic only. Return ONLY valid JSON - no markdown, no extra text.

Subject: {subject_code} - {subject_name}
Unit {unit_num}: {unit_title}
Topic: {topic_name}
Syllabus: {topic_syllabus}

SOURCE CONTEXT (PRIMARY AND ONLY REFERENCE):
{source_context}

STRICT RULE: Use ONLY the source content above. Do NOT use any outside knowledge, training data, or general knowledge. If something is not in the source, do not include it.

Return this JSON:
{{
  "topic_num": "{topic_num}",
  "topic_name": "{topic_name}",
  "subtopics": [
    {{
      "num": "{topic_num}.1",
      "name": "Subtopic Name",
      "explanation": "4-5 lines: what it is, how it works, why it matters.",
      "definition": "One formal definition sentence.",
      "real_world_example_title": "Product or system name",
      "real_world_example_text": "2 lines on how product uses this concept.",
      "algorithm_steps": ["Step 1: ...", "Step 2: ...", "Step 3: ..."],
      "worked_example": "Step-by-step example with real inputs and outputs.",
      "complexity": [
        {{"case": "Best", "time": "O(?)", "space": "O(?)", "reason": "reason"}},
        {{"case": "Average", "time": "O(?)", "space": "O(?)", "reason": "reason"}},
        {{"case": "Worst", "time": "O(?)", "space": "O(?)", "reason": "reason"}}
      ],
      "advantages": ["Advantage 1", "Advantage 2"],
      "disadvantages": ["Disadvantage 1", "Disadvantage 2"],
      "ptr_points": ["Definition point", "How it works", "Application point", "EXAM TRAP: mistake", "Key exam point"]
    }},
    {{
      "num": "{topic_num}.2",
      "name": "Second Subtopic Name",
      "explanation": "4-5 lines explanation.",
      "definition": "One formal definition.",
      "real_world_example_title": "Product name",
      "real_world_example_text": "2 lines.",
      "algorithm_steps": [],
      "worked_example": "Step-by-step example.",
      "complexity": [
        {{"case": "Best", "time": "O(?)", "space": "O(?)", "reason": "reason"}},
        {{"case": "Average", "time": "O(?)", "space": "O(?)", "reason": "reason"}},
        {{"case": "Worst", "time": "O(?)", "space": "O(?)", "reason": "reason"}}
      ],
      "advantages": ["Advantage 1", "Advantage 2"],
      "disadvantages": ["Disadvantage 1", "Disadvantage 2"],
      "ptr_points": ["Definition point", "How it works", "Application point", "EXAM TRAP: mistake", "Key exam point"]
    }}
  ],
  "video_refs": ["Search: 'keyword topic' - Covers: description"]
}}
RULES: 2 subtopics. algorithm_steps empty [] if no algorithm. worked_example with real values. Return ONLY valid JSON.
"""

NOTES_META_PROMPT = """
Anna University academic note writer. Regulation: R-2021.
Generate exam questions and master points for ONE unit. Return ONLY valid JSON.

Subject: {subject_code} - {subject_name}
Unit {unit_num}: {unit_title}
Syllabus: {unit_syllabus}

Return this JSON:
{{
  "master_points": ["IMPORTANT: point 1","FREQUENTLY ASKED: point 2","EXAM TRAP: point 3","2-MARK READY: point 4","IMPORTANT: point 5","FREQUENTLY ASKED: point 6","EXAM TRAP: point 7","2-MARK READY: point 8"],
  "exam_questions": {{
    "part_a": [
      {{"num": "Q1.", "question": "Define X."}},{{"num": "Q2.", "question": "List types of Y."}},
      {{"num": "Q3.", "question": "What is Z?"}},{{"num": "Q4.", "question": "Differentiate A and B."}},
      {{"num": "Q5.", "question": "State the algorithm for X."}},{{"num": "Q6.", "question": "Give example of Y."}},
      {{"num": "Q7.", "question": "What are advantages of Z?"}},{{"num": "Q8.", "question": "Define and explain A."}}
    ],
    "part_b": [
      {{"num": "Q1.", "question": "Explain X in detail with diagram."}},
      {{"num": "Q2.", "question": "Describe Y with worked example."}},
      {{"num": "Q3.", "question": "Compare A and B with examples."}},
      {{"num": "Q4.", "question": "Explain algorithm for Z with trace."}}
    ],
    "part_c": [
      {{"num": "Q1.", "question": "Explain the complete pipeline of X covering all subtopics with diagrams."}},
      {{"num": "Q2.", "question": "Discuss Y in detail. Derive and trace the algorithm."}}
    ]
  }}
}}
RULES: part_a exactly 8, part_b exactly 4, part_c exactly 2. Return ONLY valid JSON.
"""

BEYOND_PROMPT = """
Anna University academic content designer. Regulation: R-2021.
Generate Beyond Syllabus + Exam Focus for ALL 5 UNITS. Return ONLY valid JSON.

Subject: {subject_code} - {subject_name}
Syllabus: {syllabus_text}

JSON format:
{{
  "course_outcomes": [
    {{"co": "CO1", "outcome": "verb + outcome"}},{{"co": "CO2", "outcome": "verb + outcome"}},
    {{"co": "CO3", "outcome": "verb + outcome"}},{{"co": "CO4", "outcome": "verb + outcome"}},
    {{"co": "CO5", "outcome": "verb + outcome"}}
  ],
  "co_po_mapping": [
    {{"co":"CO1","po1":1,"po2":2,"po3":1,"po4":3,"po5":3,"po6":0,"po7":0,"po8":0,"po9":1,"po10":1,"po11":1,"po12":3,"pso1":1,"pso2":2}},
    {{"co":"CO2","po1":3,"po2":1,"po3":2,"po4":3,"po5":3,"po6":0,"po7":0,"po8":0,"po9":3,"po10":2,"po11":3,"po12":2,"pso1":2,"pso2":3}},
    {{"co":"CO3","po1":1,"po2":1,"po3":3,"po4":2,"po5":2,"po6":0,"po7":0,"po8":0,"po9":3,"po10":1,"po11":1,"po12":2,"pso1":2,"pso2":3}},
    {{"co":"CO4","po1":3,"po2":2,"po3":1,"po4":2,"po5":2,"po6":0,"po7":0,"po8":0,"po9":1,"po10":1,"po11":3,"po12":1,"pso1":3,"pso2":2}},
    {{"co":"CO5","po1":1,"po2":3,"po3":2,"po4":1,"po5":2,"po6":0,"po7":0,"po8":0,"po9":1,"po10":2,"po11":3,"po12":1,"pso1":3,"pso2":2}}
  ],
  "units": [
    {{"unit_num":"I","unit_title":"Title","hours":9,"syllabus_text":"text",
      "beyond_syllabus":[{{"topic":"T1","explanation":"2 lines."}},{{"topic":"T2","explanation":"2 lines."}},{{"topic":"T3","explanation":"2 lines."}}],
      "exam_focus":[{{"priority":"High Priority","topic":"T1","note":"note"}},{{"priority":"High Priority","topic":"T2","note":"note"}},{{"priority":"High Priority","topic":"T3","note":"note"}},{{"priority":"Medium","topic":"T4","note":"note"}},{{"priority":"Medium","topic":"T5","note":"note"}},{{"priority":"Lower","topic":"T6","note":"note"}}]}},
    {{"unit_num":"II","unit_title":"Title","hours":9,"syllabus_text":"text","beyond_syllabus":[{{"topic":"T1","explanation":"2 lines."}},{{"topic":"T2","explanation":"2 lines."}},{{"topic":"T3","explanation":"2 lines."}}],"exam_focus":[{{"priority":"High Priority","topic":"T1","note":"note"}},{{"priority":"High Priority","topic":"T2","note":"note"}},{{"priority":"High Priority","topic":"T3","note":"note"}},{{"priority":"Medium","topic":"T4","note":"note"}},{{"priority":"Medium","topic":"T5","note":"note"}},{{"priority":"Lower","topic":"T6","note":"note"}}]}},
    {{"unit_num":"III","unit_title":"Title","hours":9,"syllabus_text":"text","beyond_syllabus":[{{"topic":"T1","explanation":"2 lines."}},{{"topic":"T2","explanation":"2 lines."}},{{"topic":"T3","explanation":"2 lines."}}],"exam_focus":[{{"priority":"High Priority","topic":"T1","note":"note"}},{{"priority":"High Priority","topic":"T2","note":"note"}},{{"priority":"High Priority","topic":"T3","note":"note"}},{{"priority":"Medium","topic":"T4","note":"note"}},{{"priority":"Medium","topic":"T5","note":"note"}},{{"priority":"Lower","topic":"T6","note":"note"}}]}},
    {{"unit_num":"IV","unit_title":"Title","hours":9,"syllabus_text":"text","beyond_syllabus":[{{"topic":"T1","explanation":"2 lines."}},{{"topic":"T2","explanation":"2 lines."}},{{"topic":"T3","explanation":"2 lines."}}],"exam_focus":[{{"priority":"High Priority","topic":"T1","note":"note"}},{{"priority":"High Priority","topic":"T2","note":"note"}},{{"priority":"High Priority","topic":"T3","note":"note"}},{{"priority":"Medium","topic":"T4","note":"note"}},{{"priority":"Medium","topic":"T5","note":"note"}},{{"priority":"Lower","topic":"T6","note":"note"}}]}},
    {{"unit_num":"V","unit_title":"Title","hours":9,"syllabus_text":"text","beyond_syllabus":[{{"topic":"T1","explanation":"2 lines."}},{{"topic":"T2","explanation":"2 lines."}},{{"topic":"T3","explanation":"2 lines."}}],"exam_focus":[{{"priority":"High Priority","topic":"T1","note":"note"}},{{"priority":"High Priority","topic":"T2","note":"note"}},{{"priority":"High Priority","topic":"T3","note":"note"}},{{"priority":"Medium","topic":"T4","note":"note"}},{{"priority":"Medium","topic":"T5","note":"note"}},{{"priority":"Lower","topic":"T6","note":"note"}}]}}
  ],
  "textbooks": [{{"authors":"Authors","title":"Title","publisher":"Publisher","year":"Year","coverage":"coverage"}}]
}}
Return ONLY valid JSON. 5 units exactly. 5 COs exactly.
"""

QA_PART_A_PROMPT = """
Anna University exam question writer. Regulation: R-2021.
Generate Part A (2-mark) questions with answers for ONE unit. Return ONLY valid JSON.
Subject: {subject_code} - {subject_name}
Unit {unit_num}: {unit_title}
Syllabus: {syllabus_text}
Return this JSON:
{{
  "part_a": [
    {{"q_num":"Q1","question":"question","star":"***","answer":"2 line answer"}},
    {{"q_num":"Q2","question":"question","star":"***","answer":"2 line answer"}},
    {{"q_num":"Q3","question":"question","star":"**","answer":"2 line answer"}},
    {{"q_num":"Q4","question":"question","star":"**","answer":"2 line answer"}},
    {{"q_num":"Q5","question":"question","star":"**","answer":"2 line answer"}},
    {{"q_num":"Q6","question":"question","star":"*","answer":"2 line answer"}},
    {{"q_num":"Q7","question":"question","star":"*","answer":"2 line answer"}},
    {{"q_num":"Q8","question":"question","star":"*","answer":"2 line answer"}},
    {{"q_num":"Q9","question":"question","star":"*","answer":"2 line answer"}},
    {{"q_num":"Q10","question":"question","star":"*","answer":"2 line answer"}}
  ]
}}
Rules: exactly 10 questions, 2-line answers. Return ONLY valid JSON.
"""

QA_PART_B_PROMPT = """
Anna University exam question writer. Regulation: R-2021.
Generate Part B (13-mark) questions with model answers for ONE unit. Return ONLY valid JSON.
Subject: {subject_code} - {subject_name}
Unit {unit_num}: {unit_title}
Syllabus: {syllabus_text}
Return this JSON:
{{
  "part_b": [
    {{"q_num":"Q1","question":"full question","star":"***","introduction":"2 line intro","sections":[{{"heading":"H1","content":"3 lines"}},{{"heading":"H2","content":"3 lines"}}],"points":["point 1","point 2"],"conclusion":"1 line conclusion"}},
    {{"q_num":"Q2","question":"full question","star":"***","introduction":"2 line intro","sections":[{{"heading":"H1","content":"3 lines"}},{{"heading":"H2","content":"3 lines"}}],"points":["point 1","point 2"],"conclusion":"1 line conclusion"}},
    {{"q_num":"Q3","question":"full question","star":"**","introduction":"2 line intro","sections":[{{"heading":"H1","content":"3 lines"}},{{"heading":"H2","content":"3 lines"}}],"points":["point 1","point 2"],"conclusion":"1 line conclusion"}},
    {{"q_num":"Q4","question":"full question","star":"**","introduction":"2 line intro","sections":[{{"heading":"H1","content":"3 lines"}},{{"heading":"H2","content":"3 lines"}}],"points":["point 1","point 2"],"conclusion":"1 line conclusion"}},
    {{"q_num":"Q5","question":"full question","star":"*","introduction":"2 line intro","sections":[{{"heading":"H1","content":"3 lines"}},{{"heading":"H2","content":"3 lines"}}],"points":["point 1","point 2"],"conclusion":"1 line conclusion"}}
  ]
}}
Rules: exactly 5 questions with full model answers. Return ONLY valid JSON.
"""

QA_PART_C_PROMPT = """
Anna University exam question writer. Regulation: R-2021.
Generate Part C (15-mark) application / case-study question with a model answer for ONE unit. Return ONLY valid JSON.
Subject: {subject_code} - {subject_name}
Unit {unit_num}: {unit_title}
Syllabus: {syllabus_text}
Return this JSON:
{{
  "part_c": [
    {{"q_num":"Q1","question":"application, design or case-study question","star":"***","introduction":"2 line intro","sections":[{{"heading":"H1","content":"4 lines"}},{{"heading":"H2","content":"4 lines"}},{{"heading":"H3","content":"4 lines"}}],"points":["point 1","point 2","point 3"],"conclusion":"1 line conclusion"}}
  ]
}}
Rules: exactly 1 question that applies the unit's concepts to a real problem, with a full model answer. Return ONLY valid JSON.
"""

VERIFIED_NOTES_PROMPT = """
Act as a professor at Anna University, Chennai.
You are an expert teaching {subject_code} - {subject_name} under Regulation R-2021
for AI&ML, CSE, IT, AI&DS and Cyber Security departments.
Your students are low to average level BE students who need detailed, easy-to-understand notes.
UNDERSTAND the verified source content provided and use it as PRIMARY reference.
Refer to textbooks given in the Anna University syllabus.
UNDERSTAND SUBSCRIPT, SUPERSCRIPT and FORMULA - represent clearly in plain text.
For example: x squared = x^2, log base 2 of n = log2(n), P(w|h) = count(w,h)/count(h).
Cover Anna University question patterns - 2-mark definitions, 13-mark and 15-mark detailed answers.
Generate notes for ONE topic only. Return ONLY valid JSON - no markdown, no extra text.

Subject: {subject_code} - {subject_name}
Unit {unit_num}: {unit_title}
Topic: {topic_name}
Syllabus: {topic_syllabus}

Verified Source Content (PRIMARY AND ONLY REFERENCE):
{source_context}

STRICT RULE: Use ONLY the source content above. Do NOT use any outside knowledge, training data, or general knowledge. Every word must come from the source provided.

Return this JSON:
{{
  "topic_num": "{topic_num}",
  "topic_name": "{topic_name}",
  "what_is": "5-6 lines plain English explanation of what this topic is. Start from basics. Use a simple real-world analogy that any student can relate to.",
  "why_needed": "3-4 lines explaining why we need this in NLP, what problem it solves, and why it is important.",
  "simple_definition": "Full academic definition covering all key aspects in 3-4 lines. Must be precise enough for a 2-mark exam answer.",
  "key_features": [
    {{"feature": "Feature 1 Name", "explanation": "3 lines explaining this feature in detail with how it works", "example": "Specific real example"}},
    {{"feature": "Feature 2 Name", "explanation": "3 lines explaining this feature in detail with how it works", "example": "Specific real example"}},
    {{"feature": "Feature 3 Name", "explanation": "3 lines explaining this feature in detail", "example": "Real example"}},
    {{"feature": "Feature 4 Name", "explanation": "3 lines explaining this feature in detail", "example": "Real example"}}
  ],
  "types_or_variants": [
    {{"type": "Type 1 Name", "description": "3 lines describing this type in detail, how it works, when to use it", "example": "Specific real example"}},
    {{"type": "Type 2 Name", "description": "3 lines describing this type in detail, how it works, when to use it", "example": "Specific real example"}},
    {{"type": "Type 3 Name", "description": "3 lines describing this type", "example": "Real example"}}
  ],
  "algorithm": {{
    "name": "Full Algorithm Name",
    "input": "Detailed description of what the algorithm takes as input",
    "output": "Detailed description of what the algorithm produces as output",
    "steps": [
      "Step 1: Detailed explanation of what happens in this step and why - 2 sentences",
      "Step 2: Detailed explanation of what happens in this step and why - 2 sentences",
      "Step 3: Detailed explanation of what happens in this step and why - 2 sentences",
      "Step 4: Detailed explanation of what happens in this step and why - 2 sentences",
      "Step 5: Detailed explanation of what happens in this step and why - 2 sentences"
    ],
    "worked_example": "Complete step-by-step trace using real data. Show the input, each step applied to the input with actual values, and the final output. Minimum 8 lines."
  }},
  "mathematical_foundation": "All key formulas and equations for this topic written in plain text. Explain each formula - what each variable means, what the formula computes, and give a numerical example. Minimum 5-6 lines.",
  "advantages": [
    "Advantage 1 - 2 line explanation of why this is an advantage and in what scenarios",
    "Advantage 2 - 2 line explanation",
    "Advantage 3 - 2 line explanation",
    "Advantage 4 - 2 line explanation"
  ],
  "disadvantages": [
    "Disadvantage 1 - 2 line explanation of the limitation and its impact",
    "Disadvantage 2 - 2 line explanation",
    "Disadvantage 3 - 2 line explanation",
    "Disadvantage 4 - 2 line explanation"
  ],
  "real_world_applications": [
    {{"application": "Application 1 Name", "how": "3 lines explaining how this topic is used in this application", "tool": "Specific tool name e.g. NLTK, spaCy, Google Search"}},
    {{"application": "Application 2 Name", "how": "3 lines detailed explanation", "tool": "Tool name"}},
    {{"application": "Application 3 Name", "how": "3 lines detailed explanation", "tool": "Tool name"}}
  ],
  "connections": "4-5 lines explaining how this topic connects to other topics in AL3501. Mention specific unit numbers and topic names.",
  "exam_focus": "3-4 lines on exactly what Anna University asks about this topic. Include typical 2-mark, 13-mark and 15-mark question patterns.",
  "tips": "3-4 memory tricks, mnemonics, or exam strategies specific to this topic.",
  "ptr_points": [
    "Point 1 - Full definition ready for 2-mark answer",
    "Point 2 - How it works - key mechanism in one line",
    "Point 3 - Main application with example",
    "Point 4 - Key formula or algorithm step",
    "EXAM TRAP: Common mistake students make and how to avoid it",
    "2-MARK READY: One line precise definition",
    "13/15-MARK HINT: Introduction, Definition, Types, Algorithm, Example, Applications, Advantages, Conclusion",
    "Point 8 - Important comparison or differentiation",
    "Point 9 - Real world connection",
    "Point 10 - Most frequently asked exam point"
  ]
}}
RULES:
- algorithm steps: empty list [] only if absolutely no algorithm exists
- types_or_variants: minimum 2, maximum 4
- worked_example: MUST use real data, not placeholders like X or Y
- Every section must be detailed and complete
- Use verified source content as primary reference
- Return ONLY valid JSON
"""


# PDF BUILDERS
def build_notes_pdf(output_path,subject_code,subject_name,unit_num,subject_type,data,styles):
    unit_title=data.get("unit_title",f"Unit {unit_num}"); ltpc=data.get("ltpc","3 0 0 3")
    footer_fn=make_footer_notes(subject_code,subject_name,unit_num)
    doc=SimpleDocTemplate(output_path,pagesize=A4,leftMargin=MARGIN,rightMargin=MARGIN,topMargin=MARGIN,bottomMargin=0.75*inch)
    story=[]; story+=title_page_notes(subject_code,subject_name,unit_num,unit_title,subject_type,ltpc,styles)
    story.append(Paragraph(f"UNIT {unit_num} - {unit_title.upper()}",styles["unit_head"]))
    story.append(HRFlowable(width=CONTENT_W,color=NAVY,thickness=2)); story.append(Spacer(1,10))
    for topic in data.get("topics",[]):
        story.append(Spacer(1,6)); story.append(NavyBar(f"{topic['topic_num']}  {topic['topic_name']}")); story.append(Spacer(1,8))
        for sub in topic.get("subtopics",[]):
            story.append(Paragraph(f"<b>{sub['num']}  {sub['name']}</b>",styles["subtopic"])); story.append(Spacer(1,4))
            story.append(Paragraph("<b>Concept Explanation:</b>",styles["label"]))
            story.append(Paragraph(sub.get("explanation",""),styles["body"])); story.append(Spacer(1,4))
            if sub.get("definition"):
                story.append(defn_box(sub["definition"],styles)); story.append(Spacer(1,6))
            if sub.get("real_world_example_title"):
                story.append(Paragraph(f"<b>Real-World Example: {sub['real_world_example_title']}</b>",styles["label"]))
                story.append(Paragraph(sub.get("real_world_example_text",""),styles["body"])); story.append(Spacer(1,4))
            if sub.get("algorithm_steps"):
                story.append(Paragraph("<b>Algorithm / Pseudocode:</b>",styles["label"]))
                story.append(algo_box(sub.get("name","Algorithm"),sub["algorithm_steps"],styles)); story.append(Spacer(1,4))
            if sub.get("worked_example"):
                story.append(worked_example_box(sub.get("name","Example"),sub["worked_example"],styles)); story.append(Spacer(1,4))
            if sub.get("complexity"):
                story.append(Paragraph("<b>Complexity Analysis:</b>",styles["label"]))
                headers=["Case","Time Complexity","Space Complexity","Reason"]
                rows=[[c["case"],c["time"],c["space"],c["reason"]] for c in sub["complexity"]]
                story.append(make_table(headers,rows,col_widths=[55,90,90,CONTENT_W-235])); story.append(Spacer(1,4))
            adv=sub.get("advantages",[]); dis=sub.get("disadvantages",[])
            if adv or dis:
                story.append(Paragraph("<b>Advantages and Disadvantages:</b>",styles["label"]))
                rows=[]
                for i in range(max(len(adv),len(dis))):
                    a=adv[i] if i<len(adv) else ""; d=dis[i] if i<len(dis) else ""
                    rows.append([a,d])
                story.append(make_table(["Advantage","Disadvantage"],rows,col_widths=[CONTENT_W/2,CONTENT_W/2])); story.append(Spacer(1,4))
            if sub.get("ptr_points"):
                story.append(ptr_box(sub["ptr_points"],styles)); story.append(Spacer(1,8))
            story.append(DottedLine()); story.append(Spacer(1,6))
        if topic.get("video_refs"):
            story.append(Spacer(1,4)); story.append(NavyBar("VIDEO REFERENCES",height=20,fontsize=10)); story.append(Spacer(1,4))
            for vi,vref in enumerate(topic["video_refs"],1):
                story.append(Paragraph(f"Video {vi}: YouTube - {vref}",styles["bullet"]))
            story.append(Spacer(1,8))
    story.append(PageBreak()); story.append(NavyBar("MASTER POINTS TO REMEMBER")); story.append(Spacer(1,6))
    if data.get("master_points"): story.append(ptr_box(data["master_points"],styles,master=True))
    story.append(Spacer(1,10)); story.append(NavyBar("EXPECTED EXAM QUESTIONS")); story.append(Spacer(1,6))
    eq=data.get("exam_questions",{})
    for section_label,section_key in [("PART A - 2 Mark Questions","part_a"),("PART B - 13 Mark Questions","part_b"),("PART C - 15 Mark Questions","part_c")]:
        story.append(NavyBar(section_label,height=22,fontsize=10)); story.append(Spacer(1,4))
        for q in eq.get(section_key,[]): story.append(Paragraph(f"<b>{q['num']}</b> {q['question']}",styles["exam_q"]))
        story.append(Spacer(1,6))
    doc.build(story,onFirstPage=footer_fn,onLaterPages=footer_fn)
    print(f"    Saved: {output_path}")

def build_qa_pdf(output_path,subject_code,subject_name,unit_num,unit_title,data,styles):
    footer_fn=make_footer_qa(subject_code,subject_name,unit_num)
    doc=SimpleDocTemplate(output_path,pagesize=A4,leftMargin=MARGIN,rightMargin=MARGIN,topMargin=MARGIN,bottomMargin=0.75*inch)
    story=[]; story+=title_page_qa(subject_code,subject_name,unit_num,unit_title,styles)
    story.append(NavyBar("PART A - 2 MARK QUESTIONS WITH ANSWERS")); story.append(Spacer(1,10))
    story.append(legend_box(styles)); story.append(Spacer(1,12))
    for item in data.get("part_a",[]):
        story.append(q_header_table(f"{item['q_num']}. {item['question']}",PART_A.marks,item["star"],styles))
        story.append(Paragraph("<b>Answer:</b>",styles["answer_label"]))
        story.append(Paragraph(item["answer"],styles["answer"])); story.append(DottedLine()); story.append(Spacer(1,6))
    for part, title in ((PART_B, "PART B"), (PART_C, "PART C")):
        items=data.get(part.key,[])
        if not items: continue
        story.append(PageBreak()); story.append(NavyBar(f"{title} - {part.marks} MARK QUESTIONS WITH MODEL ANSWERS")); story.append(Spacer(1,10))
        story.append(legend_box(styles)); story.append(Spacer(1,12))
        _long_answers(story,items,part.marks,styles)
    doc.build(story,onFirstPage=footer_fn,onLaterPages=footer_fn)
    print(f"    Saved: {output_path}")


def _long_answers(story,items,marks,styles):
    for item in items:
        story.append(DoubleLine()); story.append(q_header_table(f"{item['q_num']}. {item['question']}",marks,item["star"],styles))
        story.append(Paragraph("<b>Answer:</b>",styles["answer_label"]))
        if item.get("introduction"): story.append(Paragraph("<b>Introduction:</b>",styles["subhead"])); story.append(Paragraph(item["introduction"],styles["answer"]))
        for sec in item.get("sections",[]): story.append(Paragraph(f"<b>{sec['heading']}:</b>",styles["subhead"])); story.append(Paragraph(sec["content"],styles["answer"]))
        for pt in item.get("points",[]): story.append(Paragraph(f"- {pt}",styles["bullet"]))
        if item.get("conclusion"): story.append(Paragraph("<b>Conclusion:</b>",styles["subhead"])); story.append(Paragraph(item["conclusion"],styles["answer"]))
        story.append(DottedLine()); story.append(Spacer(1,8))


def build_beyond_pdf(output_path,subject_code,subject_name,data,styles):
    footer_fn=make_footer_beyond(subject_code,subject_name)
    doc=SimpleDocTemplate(output_path,pagesize=A4,leftMargin=MARGIN,rightMargin=MARGIN,topMargin=MARGIN,bottomMargin=0.75*inch)
    story=[]; story+=title_page_beyond(subject_code,subject_name,styles)
    story.append(NavyBar("COURSE OUTCOMES AND CO-PO MAPPING")); story.append(Spacer(1,8))
    co_data=[["CO No.","Course Outcome"]]
    _co_cell=ParagraphStyle("co_cell",fontName="AG-Serif",fontSize=10,leading=13)   # Paragraph: wraps + renders escaped text
    for co in data.get("course_outcomes",[]): co_data.append([Paragraph(co["co"],_co_cell),Paragraph(co["outcome"],_co_cell)])
    co_table=Table(co_data,colWidths=[60,CONTENT_W-60])
    co_table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),NAVY),("TEXTCOLOR",(0,0),(-1,0),WHITE),
        ("FONTNAME",(0,0),(-1,0),"AG-Serif-Bold"),("FONTNAME",(0,1),(-1,-1),"AG-Serif"),
        ("FONTSIZE",(0,0),(-1,-1),10),("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE,LIGHT_GREY]),
        ("BOX",(0,0),(-1,-1),1,NAVY),("INNERGRID",(0,0),(-1,-1),0.5,MID_GREY),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("VALIGN",(0,0),(-1,-1),"TOP")]))
    story.append(co_table); story.append(Spacer(1,8))
    story.append(NavyBar("CO-PO MAPPING TABLE",height=22,fontsize=10)); story.append(Spacer(1,6))
    po_headers=["CO","PO1","PO2","PO3","PO4","PO5","PO6","PO7","PO8","PO9","PO10","PO11","PO12","PSO1","PSO2"]
    po_keys=["po1","po2","po3","po4","po5","po6","po7","po8","po9","po10","po11","po12","pso1","pso2"]
    mapping_data=[po_headers]
    for row in data.get("co_po_mapping",[]):
        r=[row["co"]]
        for k in po_keys: v=row.get(k,0); r.append(str(v) if v!=0 else "-")
        mapping_data.append(r)
    avg_row=["Avg"]
    for k in po_keys:
        vals=[row.get(k,0) for row in data.get("co_po_mapping",[]) if isinstance(row.get(k,0),int)]
        non_zero=[v for v in vals if v>0]
        avg=round(sum(non_zero)/len(non_zero),1) if non_zero else 0
        avg_row.append(str(avg) if avg>0 else "-")
    mapping_data.append(avg_row)
    col_w=[35]+[28]*14; mp_table=Table(mapping_data,colWidths=col_w)
    cell_styles=[("BACKGROUND",(0,0),(-1,0),NAVY),("TEXTCOLOR",(0,0),(-1,0),WHITE),
        ("FONTNAME",(0,0),(-1,0),"AG-Serif-Bold"),("FONTNAME",(0,1),(-1,-1),"AG-Serif"),
        ("FONTSIZE",(0,0),(-1,-1),8),("BOX",(0,0),(-1,-1),1,NAVY),("INNERGRID",(0,0),(-1,-1),0.3,MID_GREY),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
        ("LEFTPADDING",(0,0),(-1,-1),2),("RIGHTPADDING",(0,0),(-1,-1),2),("ALIGN",(1,1),(-1,-1),"CENTER"),
        ("BACKGROUND",(0,len(mapping_data)-1),(-1,len(mapping_data)-1),YELLOW_BG),
        ("FONTNAME",(0,len(mapping_data)-1),(-1,len(mapping_data)-1),"AG-Serif-Bold")]
    for ri,row in enumerate(mapping_data[1:-1],start=1):
        for ci,val in enumerate(row[1:],start=1):
            if val=="3": cell_styles+=[("BACKGROUND",(ci,ri),(ci,ri),DARK_GREEN_BG),("TEXTCOLOR",(ci,ri),(ci,ri),WHITE)]
            elif val=="2": cell_styles.append(("BACKGROUND",(ci,ri),(ci,ri),GREEN_BG))
            elif val=="-": cell_styles.append(("BACKGROUND",(ci,ri),(ci,ri),LIGHT_GREY))
    mp_table.setStyle(TableStyle(cell_styles)); story.append(mp_table); story.append(PageBreak())
    pri_style_map={"High Priority":"high_pri","Medium":"med_pri","Lower":"low_pri"}
    for unit in data.get("units",[]):
        story.append(NavyBar(f"UNIT {unit['unit_num']}    {unit['unit_title']}    {unit['hours']} Hours",height=28,fontsize=11))
        story.append(Spacer(1,4)); story.append(Paragraph(unit.get("syllabus_text",""),styles["syllabus"])); story.append(Spacer(1,6))
        story.append(NavyBar("BEYOND THE SYLLABUS",height=22,fontsize=10)); story.append(Spacer(1,4))
        beyond_rows=[]
        for item in unit.get("beyond_syllabus",[]):
            beyond_rows.append([Paragraph(item["topic"],styles["beyond_topic"]),Paragraph(item["explanation"],styles["beyond_exp"])])
        lc=4.5*cm; rc=CONTENT_W-lc; bt=Table(beyond_rows,colWidths=[lc,rc])
        bt_styles=[("BOX",(0,0),(-1,-1),0.5,MID_GREY),("INNERGRID",(0,0),(-1,-1),0.3,MID_GREY),
            ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
            ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),("VALIGN",(0,0),(-1,-1),"TOP")]
        for i in range(len(beyond_rows)):
            bg=WHITE if i%2==0 else LIGHT_BLUE; bt_styles.append(("BACKGROUND",(0,i),(-1,i),bg))
        bt.setStyle(TableStyle(bt_styles)); story.append(bt); story.append(Spacer(1,6))
        story.append(NavyBar("EXAM FOCUS",height=22,fontsize=10)); story.append(Spacer(1,4))
        ef_rows=[]
        for item in unit.get("exam_focus",[]):
            ps=styles.get(pri_style_map.get(item["priority"],"focus_note"))
            ef_rows.append([Paragraph(item["priority"],ps),Paragraph(item["topic"],styles["focus_topic"]),Paragraph(item["note"],styles["focus_note"])])
        pc=2.8*cm; tc=5.5*cm; nc=CONTENT_W-pc-tc; ef_table=Table(ef_rows,colWidths=[pc,tc,nc])
        ef_s=[("BOX",(0,0),(-1,-1),0.5,MID_GREY),("INNERGRID",(0,0),(-1,-1),0.3,MID_GREY),
            ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
            ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),("VALIGN",(0,0),(-1,-1),"TOP")]
        for i in range(len(ef_rows)):
            bg=WHITE if i%2==0 else LIGHT_BLUE; ef_s.append(("BACKGROUND",(0,i),(-1,i),bg))
        ef_table.setStyle(TableStyle(ef_s)); story.append(ef_table); story.append(PageBreak())
    story.append(NavyBar("REFERENCE BOOKS",height=22,fontsize=10)); story.append(Spacer(1,10))
    story.append(Paragraph("<b>TEXT BOOKS</b>",ParagraphStyle("tb",fontName="AG-Serif-Bold",fontSize=11,textColor=NAVY,leading=16))); story.append(Spacer(1,6))
    for i,book in enumerate(data.get("textbooks",[]),1):
        story.append(Paragraph(f"{i}. {book['authors']} - <i>{book['title']}</i> - {book['publisher']}, {book['year']}",styles["body"]))
        story.append(Paragraph(f"   {book['coverage']}",ParagraphStyle("cov",fontName="AG-Serif-Italic",fontSize=10,leading=14,textColor=MID_GREY,leftIndent=15)))
        story.append(Spacer(1,4))
    doc.build(story,onFirstPage=footer_fn,onLaterPages=footer_fn)
    print(f"    Saved: {output_path}")


def build_verified_notes_pdf(output_path,subject_code,subject_name,unit_num,subject_type,data,styles):
    unit_title=data.get("unit_title","Unit "+unit_num)
    footer_fn=make_footer_verified(subject_code,subject_name,unit_num)
    doc=SimpleDocTemplate(output_path,pagesize=A4,leftMargin=MARGIN,rightMargin=MARGIN,topMargin=MARGIN,bottomMargin=0.75*inch)
    story=[]; story+=title_page_notes(subject_code,subject_name,unit_num,unit_title,subject_type,"3 0 0 3",styles)
    story.append(NavyBar("UNIT "+unit_num+" - "+unit_title.upper()+" | Notes from Verified Sources",height=30,fontsize=12))
    story.append(Spacer(1,6))
    all_topic_names=[t.get("topic_name","") for t in data.get("topics",[])]
    story.append(Paragraph("<b>Topics Covered in This Unit:</b>",styles["label"]))
    story.append(Paragraph(" | ".join(all_topic_names),styles["syllabus"])); story.append(Spacer(1,4))
    how_data=[[Paragraph("<b>How to Use These Notes</b>  Each topic is explained in SIMPLE language with examples. DEFINITION boxes = Key definitions for 2-mark answers. ALGORITHM boxes = Step by step process for 16-mark answers. EXAM FOCUS boxes = What Anna University asks. KEY POINTS boxes = Quick revision before exam.",ParagraphStyle("how",fontName="AG-Serif",fontSize=10,leading=15,textColor=BLACK))]]
    how_table=Table(how_data,colWidths=[CONTENT_W])
    how_table.setStyle(TableStyle([("BOX",(0,0),(-1,-1),1.5,NAVY),("BACKGROUND",(0,0),(-1,-1),LIGHT_BLUE),("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10)]))
    story.append(how_table); story.append(Spacer(1,12))
    for t_idx,topic in enumerate(data.get("topics",[]),1):
        topic_name=topic.get("topic_name","Topic "+str(t_idx))
        story.append(NavyBar("TOPIC "+str(t_idx)+": "+topic_name.upper(),height=28,fontsize=11)); story.append(Spacer(1,8))
        if topic.get("what_is"):
            story.append(Paragraph("What is "+topic_name+"?",styles["section_head"]))
            story.append(Paragraph(topic["what_is"],styles["vn_body"])); story.append(Spacer(1,6))
        if topic.get("why_needed"):
            story.append(Paragraph("Why Do We Need This?",styles["section_head"]))
            story.append(Paragraph(topic["why_needed"],styles["vn_body"])); story.append(Spacer(1,6))
        if topic.get("simple_definition"):
            story.append(defn_box(topic["simple_definition"],styles)); story.append(Spacer(1,8))
        if topic.get("key_features"):
            story.append(Paragraph("Key Features",styles["section_head"]))
            headers=["Feature","Explanation","Example"]
            rows_data=[[f["feature"],f["explanation"],f["example"]] for f in topic["key_features"]]
            story.append(feature_table(headers,rows_data)); story.append(Spacer(1,8))
        if topic.get("types_or_variants"):
            story.append(Paragraph("Types and Variants",styles["section_head"]))
            headers=["Type","Description","Example"]
            rows_data=[[v["type"],v["description"],v["example"]] for v in topic["types_or_variants"]]
            story.append(feature_table(headers,rows_data)); story.append(Spacer(1,8))
        algo=topic.get("algorithm",{})
        if algo and algo.get("steps"):
            story.append(Paragraph("Algorithm / Process",styles["section_head"]))
            story.append(algo_box(algo.get("name",topic_name),algo.get("steps",[]),styles)); story.append(Spacer(1,6))
            if algo.get("worked_example"):
                story.append(worked_example_box(topic_name,algo["worked_example"],styles)); story.append(Spacer(1,6))
        if topic.get("mathematical_foundation"):
            story.append(Paragraph("Mathematical Foundation",styles["section_head"]))
            story.append(code_box(topic["mathematical_foundation"],styles)); story.append(Spacer(1,8))
        adv=topic.get("advantages",[]); dis=topic.get("disadvantages",[])
        if adv or dis:
            story.append(Paragraph("Advantages and Disadvantages",styles["section_head"]))
            story.append(adv_disadv_table(adv,dis)); story.append(Spacer(1,8))
        if topic.get("real_world_applications"):
            story.append(Paragraph("Real World Applications",styles["section_head"]))
            headers=["Application","How It Is Used","Tool / System"]
            rows_data=[[a["application"],a["how"],a["tool"]] for a in topic["real_world_applications"]]
            story.append(feature_table(headers,rows_data)); story.append(Spacer(1,8))
        if topic.get("connections"):
            story.append(Paragraph("Connections to Other Topics",styles["section_head"]))
            story.append(Paragraph(topic["connections"],styles["vn_body"])); story.append(Spacer(1,6))
        if topic.get("exam_focus"):
            story.append(exam_focus_box(topic["exam_focus"],styles)); story.append(Spacer(1,6))
        if topic.get("tips"):
            story.append(tips_box(topic["tips"],styles)); story.append(Spacer(1,6))
        if topic.get("ptr_points"):
            story.append(key_points_box(topic["ptr_points"],styles)); story.append(Spacer(1,10))
        story.append(DottedLine()); story.append(Spacer(1,10))
    story.append(PageBreak())
    story.append(NavyBar("UNIT "+unit_num+" - QUICK REVISION SUMMARY",height=28,fontsize=12)); story.append(Spacer(1,8))
    summary_rows=[]
    for topic in data.get("topics",[]):
        pts=topic.get("ptr_points",[]); key_pts=" | ".join(pts[:3]) if pts else ""
        summary_rows.append([topic.get("topic_name",""),key_pts])
    if summary_rows: story.append(feature_table(["Topic","Key Points to Remember"],summary_rows))
    story.append(Spacer(1,12))
    story.append(NavyBar("IMPORTANT ANNA UNIVERSITY QUESTIONS",height=28,fontsize=12)); story.append(Spacer(1,8))
    eq=data.get("exam_questions",{})
    for section_label,section_key in [("2-Mark Questions (Definition / Short Answer)","part_a"),("13-Mark Questions (Explain with Examples)","part_b"),("15-Mark Questions (Application / Case Study)","part_c")]:
        story.append(NavyBar(section_label,height=22,fontsize=10)); story.append(Spacer(1,6))
        for q in eq.get(section_key,[]): story.append(Paragraph("<b>"+q.get("num","")+"</b> "+q.get("question",""),styles["exam_q"]))
        story.append(Spacer(1,8))
    story.append(NavyBar("FINAL EXAM STRATEGY",height=22,fontsize=10)); story.append(Spacer(1,6))
    for pt in ["Write definitions clearly - 1 to 2 marks easy if definition is correct.",
               "Use comparison tables - examiners love organized information.",
               "For every topic: Definition, Explanation, Types, Algorithm, Example, Applications.",
               "Always mention real-world tool names - NLTK, spaCy, Stanford NLP, Google.",
               "For algorithms - always trace with a real example step by step.",
               "For 13/15-mark answers - Introduction, Body with headings, Conclusion.",
               "EXAM TRAP: Do not confuse similar terms - always define before explaining.",
               "ALL THE BEST FOR YOUR EXAMS!"]:
        story.append(Paragraph("*  "+pt,styles["bullet"]))
    story.append(Spacer(1,10))
    doc.build(story,onFirstPage=footer_fn,onLaterPages=footer_fn)
    print("    Saved: "+output_path)


# UTILITY FUNCTIONS
def auto_abbreviate(name):
    words=name.strip().split()
    return "".join(w[0].upper() for w in words if w)

def get_unit_syllabus(syllabus_text,unit_num):
    roman=["I","II","III","IV","V"]; idx=roman.index(unit_num)
    next_unit=roman[idx+1] if idx+1<len(roman) else None
    lines=syllabus_text.splitlines(); capture=False; result=[]
    for line in lines:
        upper=line.upper()
        if f"UNIT {unit_num}" in upper or f"UNIT-{unit_num}" in upper: capture=True
        elif next_unit and (f"UNIT {next_unit}" in upper or f"UNIT-{next_unit}" in upper): capture=False
        if capture: result.append(line)
    return "\n".join(result) if result else syllabus_text

def load_source_context(unit_num,topic_content):
    TEXT_DIR=Path(os.path.join("C:/","Users","johnb","Documents","Teaching","NLP AL3501","NLP Source Texts"))
    if not TEXT_DIR.exists(): return ""
    unit_file_map={
        "I":  ["unit1_origins_nlp.txt","unit1_language_modeling.txt","unit1_regular_expressions_fsa.txt","unit1_morphology_transducers.txt","unit1_tokenization_spelling.txt"],
        "II": ["unit2_ngrams_smoothing.txt","unit2_pos_tagging.txt","unit2_hmm_maxent.txt"],
        "III":["unit3_cfg_grammar.txt","unit3_dependency_parsing.txt","unit3_probabilistic_cfg.txt","unit3_feature_structures.txt"],
        "IV": ["unit4_semantic_analysis.txt","unit4_wsd.txt","unit4_wsd_yarowsky.txt","unit4_wsd_abney.txt","unit4_description_logics.txt","unit4_lexical_resources.txt","unit4_propbank_framenet.txt"],
        "V":  ["unit5_discourse_segmentation.txt","unit5_coherence.txt","unit5_reference_phenomena.txt","unit5_anaphora_resolution.txt","unit5_centering_algorithm.txt","unit5_coreference_resolution.txt","unit5_corpora_resources.txt"],
    }
    keyword_file_map={
        "origins":"unit1_origins_nlp.txt","challenges":"unit1_origins_nlp.txt",
        "language model":"unit1_language_modeling.txt","statistical":"unit1_language_modeling.txt",
        "regular expression":"unit1_regular_expressions_fsa.txt","finite state":"unit1_regular_expressions_fsa.txt",
        "morphology":"unit1_morphology_transducers.txt","transducer":"unit1_morphology_transducers.txt",
        "tokenization":"unit1_tokenization_spelling.txt","spelling":"unit1_tokenization_spelling.txt","edit distance":"unit1_tokenization_spelling.txt",
        "n-gram":"unit2_ngrams_smoothing.txt","smoothing":"unit2_ngrams_smoothing.txt","interpolation":"unit2_ngrams_smoothing.txt",
        "pos tagging":"unit2_pos_tagging.txt","part of speech":"unit2_pos_tagging.txt",
        "hidden markov":"unit2_hmm_maxent.txt","hmm":"unit2_hmm_maxent.txt","maximum entropy":"unit2_hmm_maxent.txt",
        "context free":"unit3_cfg_grammar.txt","cfg":"unit3_cfg_grammar.txt",
        "dependency":"unit3_dependency_parsing.txt","parsing":"unit3_dependency_parsing.txt",
        "probabilistic":"unit3_probabilistic_cfg.txt","pcfg":"unit3_probabilistic_cfg.txt",
        "feature structure":"unit3_feature_structures.txt","unification":"unit3_feature_structures.txt",
        "semantic":"unit4_semantic_analysis.txt","word sense":"unit4_wsd.txt","wsd":"unit4_wsd.txt",
        "yarowsky":"unit4_wsd_yarowsky.txt","bootstrapping":"unit4_wsd_yarowsky.txt","abney":"unit4_wsd_abney.txt",
        "description logic":"unit4_description_logics.txt","wordnet":"unit4_lexical_resources.txt",
        "propbank":"unit4_propbank_framenet.txt","framenet":"unit4_propbank_framenet.txt",
        "discourse":"unit5_discourse_segmentation.txt","coherence":"unit5_coherence.txt",
        "reference":"unit5_reference_phenomena.txt","anaphora":"unit5_anaphora_resolution.txt","hobbs":"unit5_anaphora_resolution.txt",
        "centering":"unit5_centering_algorithm.txt","coreference":"unit5_coreference_resolution.txt",
        "brown corpus":"unit5_corpora_resources.txt","bnc":"unit5_corpora_resources.txt","porter stemmer":"unit5_corpora_resources.txt",
    }
    topic_lower=topic_content.lower(); loaded_files=set(); context_parts=[]
    for keyword,filename in keyword_file_map.items():
        if keyword in topic_lower and filename not in loaded_files:
            txt_path=TEXT_DIR/filename
            if txt_path.exists():
                content=txt_path.read_text(encoding="utf-8").strip()
                if content: context_parts.append("[Source: "+filename+"]\n"+content); loaded_files.add(filename)
    if not context_parts and unit_num in unit_file_map:
        for filename in unit_file_map[unit_num]:
            txt_path=TEXT_DIR/filename
            if txt_path.exists() and filename not in loaded_files:
                content=txt_path.read_text(encoding="utf-8").strip()
                if content: context_parts.append("[Source: "+filename+"]\n"+content[:800]); loaded_files.add(filename)
    return "\n\n".join(context_parts[:3])

# MENUS
def select_units():
    print("\n  Which units do you want to process?")
    print("  "+"-"*45)
    print("  A -- All units (I, II, III, IV, V)")
    print("  B -- Unit I only"); print("  C -- Unit II only")
    print("  D -- Unit III only"); print("  E -- Unit IV only")
    print("  F -- Unit V only"); print("  G -- Custom (e.g. type: I,III,V)")
    print("  "+"-"*45)
    choice=input("  Enter choice: ").strip().upper()
    all_units=["I","II","III","IV","V"]
    if choice=="A": return all_units
    elif choice=="B": return ["I"]
    elif choice=="C": return ["II"]
    elif choice=="D": return ["III"]
    elif choice=="E": return ["IV"]
    elif choice=="F": return ["V"]
    elif choice=="G":
        raw=input("  Type units separated by comma (e.g. I,III,V): ").strip().upper()
        selected=[u.strip() for u in raw.split(",") if u.strip() in all_units]
        if not selected: print("  Invalid. Defaulting to all."); return all_units
        return selected
    else: print("  Invalid. Defaulting to all."); return all_units

def select_content_types():
    print("\n  What do you want to generate?")
    print("  "+"-"*45)
    print("  A -- Syllabus and Beyond"); print("  B -- Notes")
    print("  C -- Q&A"); print("  D -- Notes from Verified Sources")
    print("  E -- All of the above"); print("  F -- Custom (e.g. type: A,B,D)")
    print("  "+"-"*45)
    choice=input("  Enter choice: ").strip().upper()
    if choice=="A": return {"beyond":True,"notes":False,"qa":False,"verified":False}
    elif choice=="B": return {"beyond":False,"notes":True,"qa":False,"verified":False}
    elif choice=="C": return {"beyond":False,"notes":False,"qa":True,"verified":False}
    elif choice=="D": return {"beyond":False,"notes":False,"qa":False,"verified":True}
    elif choice=="E": return {"beyond":True,"notes":True,"qa":True,"verified":True}
    elif choice=="F":
        raw=input("  Type choices (e.g. A,B,D): ").strip().upper()
        parts=[p.strip() for p in raw.split(",")]
        return {"beyond":"A" in parts,"notes":"B" in parts,"qa":"C" in parts,"verified":"D" in parts}
    else: print("  Invalid. Defaulting to all."); return {"beyond":True,"notes":True,"qa":True,"verified":True}


# MAIN
def main():
    print("\n"+"="*62)
    print("  Anna University - Academic PDF Generator")
    print("  Dhanalakshmi College of Engineering")
    print("="*62+"\n")
    api_key=os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set!")
        print("Run: setx GROQ_API_KEY \"your-key-here\"")
        print("Then close and reopen Anaconda Prompt.")
        sys.exit(1)
    client=_GroqClient(api_key=api_key); styles=get_styles()
    print("-- Subject Details -----------------------------------------")
    subject_code=input("  Subject Code (e.g. AL3501): ").strip().upper()
    subject_name=input("  Subject Name (e.g. Natural Language Processing): ").strip()
    abbr=auto_abbreviate(subject_name)
    confirm=input(f"  Detected abbreviation: {abbr}  - Press Enter to confirm or type new: ").strip()
    if confirm: abbr=confirm.upper()
    print("\n  Subject Type:")
    print("    1 - Core / Theory"); print("    2 - Coding")
    type_choice=input("  Enter 1 or 2: ").strip()
    subject_type="coding" if type_choice=="2" else "core"
    print("\n  Paste full syllabus (all 5 units). Type END on new line when done:\n")
    lines=[]
    while True:
        line=input()
        if line.strip().upper()=="END": break
        lines.append(line)
    syllabus_text="\n".join(lines)
    folder_name=f"{abbr} {subject_code}"; output_folder=Path(OUTPUT_BASE)/folder_name
    output_folder.mkdir(parents=True,exist_ok=True)
    print(f"\nOutput folder: {output_folder}")
    print("\nWhich steps to run?")
    print("  1 - All steps (choose content types below)")
    print("  2 - Only Q&A PDFs"); print("  3 - Only Notes PDFs")
    print("  4 - Only Syllabus and Beyond PDF"); print("  5 - Notes from Verified Sources")
    step_choice=input("  Enter choice (1/2/3/4/5): ").strip()
    content_types={"beyond":False,"notes":False,"qa":False,"verified":False}
    if step_choice=="1": content_types=select_content_types()
    elif step_choice=="2": content_types["qa"]=True
    elif step_choice=="3": content_types["notes"]=True
    elif step_choice=="4": content_types["beyond"]=True
    elif step_choice=="5": content_types["verified"]=True
    selected_units=select_units()
    print("\n  Selected units: "+", ".join(selected_units))
    run_beyond=content_types["beyond"]; run_notes=content_types["notes"]
    run_qa=content_types["qa"]; run_verified=content_types["verified"]
    beyond_data=None; failures=[]

    # STEP 1 - BEYOND
    if run_beyond:
        print("\n"+"-"*62); print("  STEP 1 - Generating Syllabus and Beyond PDF"); print("-"*62)
        try:
            prompt=BEYOND_PROMPT.format(subject_code=subject_code,subject_name=subject_name,syllabus_text=syllabus_text)
            beyond_data=call_gemini(client,prompt)
            beyond_path=output_folder/f"{abbr} {subject_code}_SyllabusAndBeyond_ExamFocus.pdf"
            build_beyond_pdf(str(beyond_path),subject_code,subject_name,beyond_data,styles)
        except Exception as e: print(f"  Error: {e}"); failures.append(f"Error: {e}")
        time.sleep(10)

    # STEP 2 - NOTES
    if run_notes:
        print("\n"+"-"*62); print("  STEP 2 - Generating Student Notes PDFs"); print("-"*62)
        for unit_num,unit_hours in UNITS_INFO:
            if unit_num not in selected_units: continue
            print(f"\n  Unit {unit_num}...")
            unit_title=f"Unit {unit_num}"
            if beyond_data:
                for u in beyond_data.get("units",[]):
                    if u.get("unit_num")==unit_num: unit_title=u.get("unit_title",unit_title); break
            unit_syllabus=get_unit_syllabus(syllabus_text,unit_num)
            try:
                topic_lines=[l.strip() for l in unit_syllabus.replace("--","-").replace("\u2013","-").split("-") if l.strip() and len(l.strip())>5]
                if len(topic_lines)<2: topic_lines=[l.strip() for l in unit_syllabus.split(",") if l.strip() and len(l.strip())>5]
                topics_per_call=max(1,len(topic_lines)//3); topic_groups=[]
                for i in range(0,len(topic_lines),topics_per_call):
                    group=", ".join(topic_lines[i:i+topics_per_call])
                    if group: topic_groups.append(group)
                topic_groups=topic_groups[:4]; all_topics=[]
                for t_idx,topic_content in enumerate(topic_groups,1):
                    print(f"    Topic {t_idx}/{len(topic_groups)}...")
                    source_context=load_source_context(unit_num,topic_content)
                    if not source_context:
                        print(f"    SKIP: No verified source found for: {topic_content[:50]}")
                        continue
                    print(f"    Using verified source context...")
                    t_prompt=NOTES_TOPIC_PROMPT.format(subject_code=subject_code,subject_name=subject_name,unit_num=unit_num,unit_title=unit_title,topic_num=str(t_idx),topic_name=topic_content[:60],topic_syllabus=topic_content,source_context=source_context)
                    t_data=call_gemini(client,t_prompt); all_topics.append(t_data); time.sleep(8)
                print(f"    Generating exam questions...")
                meta_prompt=NOTES_META_PROMPT.format(subject_code=subject_code,subject_name=subject_name,unit_num=unit_num,unit_title=unit_title,unit_syllabus=unit_syllabus[:500])
                meta_data=call_gemini(client,meta_prompt); time.sleep(8)
                notes_data={"unit_title":unit_title,"ltpc":"3 0 0 3","topics":all_topics,"master_points":meta_data.get("master_points",[]),"exam_questions":meta_data.get("exam_questions",{})}
                notes_path=output_folder/f"{abbr} {subject_code}_Unit{unit_num}_Notes.pdf"
                build_notes_pdf(str(notes_path),subject_code,subject_name,unit_num,subject_type,notes_data,styles)
            except Exception as e: print(f"  Error Unit {unit_num} Notes: {e}"); failures.append(f"Error Unit {unit_num} Notes: {e}")
            time.sleep(10)

    # STEP 3 - Q&A
    if run_qa:
        print("\n"+"-"*62); print("  STEP 3 - Generating Important Q&A PDFs"); print("-"*62)
        for unit_num,unit_hours in UNITS_INFO:
            if unit_num not in selected_units: continue
            print(f"\n  Unit {unit_num}...")
            unit_title=f"Unit {unit_num}"
            if beyond_data:
                for u in beyond_data.get("units",[]):
                    if u.get("unit_num")==unit_num: unit_title=u.get("unit_title",unit_title); break
            try:
                prompt_a=QA_PART_A_PROMPT.format(subject_code=subject_code,subject_name=subject_name,unit_num=unit_num,unit_title=unit_title,syllabus_text=get_unit_syllabus(syllabus_text,unit_num)[:400])
                print(f"    Generating Part A questions..."); qa_data_a=call_json_expect(client,prompt_a,"part_a",PART_A.count); time.sleep(8)
                prompt_b=QA_PART_B_PROMPT.format(subject_code=subject_code,subject_name=subject_name,unit_num=unit_num,unit_title=unit_title,syllabus_text=get_unit_syllabus(syllabus_text,unit_num)[:400])
                print(f"    Generating Part B questions..."); qa_data_b=call_json_expect(client,prompt_b,"part_b",PART_B.count); time.sleep(8)
                prompt_c=QA_PART_C_PROMPT.format(subject_code=subject_code,subject_name=subject_name,unit_num=unit_num,unit_title=unit_title,syllabus_text=get_unit_syllabus(syllabus_text,unit_num)[:400])
                print(f"    Generating Part C question..."); qa_data_c=call_json_expect(client,prompt_c,"part_c",PART_C.count); time.sleep(8)
                for item in qa_data_a.get("part_a",[]): item["star"]=item["star"].replace("***","SSS").replace("**","SS").replace("*","S").replace("SSS","***").replace("SS","**").replace("S","*")
                for item in qa_data_b.get("part_b",[]): item["star"]=item["star"].replace("***","SSS").replace("**","SS").replace("*","S").replace("SSS","***").replace("SS","**").replace("S","*")
                for item in qa_data_c.get("part_c",[]): item["star"]=item.get("star","")
                qa_data={"part_a":qa_data_a.get("part_a",[]),"part_b":qa_data_b.get("part_b",[]),"part_c":qa_data_c.get("part_c",[])}
                qa_path=output_folder/f"{abbr} {subject_code}_Unit{unit_num}_ImportantQA.pdf"
                build_qa_pdf(str(qa_path),subject_code,subject_name,unit_num,unit_title,qa_data,styles)
            except Exception as e: print(f"  Error Unit {unit_num} Q&A: {e}"); failures.append(f"Error Unit {unit_num} Q&A: {e}")
            time.sleep(10)

    # STEP 4 - VERIFIED NOTES
    if run_verified:
        print("\n"+"-"*62); print("  STEP 4 - Generating Notes from Verified Sources"); print("-"*62)
        for unit_num,unit_hours in UNITS_INFO:
            if unit_num not in selected_units: continue
            print(f"\n  Unit {unit_num}...")
            unit_title="Unit "+unit_num
            if beyond_data:
                for u in beyond_data.get("units",[]):
                    if u.get("unit_num")==unit_num: unit_title=u.get("unit_title",unit_title); break
            unit_syllabus=get_unit_syllabus(syllabus_text,unit_num)
            try:
                topic_lines=[l.strip() for l in unit_syllabus.replace("-",",").split(",") if l.strip() and len(l.strip())>5]
                topics_per_call=max(1,len(topic_lines)//3); topic_groups=[]
                for i in range(0,len(topic_lines),topics_per_call):
                    group=", ".join(topic_lines[i:i+topics_per_call])
                    if group: topic_groups.append(group)
                topic_groups=topic_groups[:4]; all_topics=[]; master_points=[]
                for t_idx,topic_content in enumerate(topic_groups,1):
                    print(f"    Topic {t_idx}/{len(topic_groups)}...")
                    source_context=load_source_context(unit_num,topic_content)
                    if not source_context:
                        print(f"    SKIP: No verified source for: {topic_content[:50]}")
                        continue
                    print(f"    Using verified source context...")
                    t_prompt=VERIFIED_NOTES_PROMPT.format(subject_code=subject_code,subject_name=subject_name,unit_num=unit_num,unit_title=unit_title,topic_num=str(t_idx),topic_name=topic_content[:60],topic_syllabus=topic_content,source_context=source_context)
                    t_data=call_gemini(client,t_prompt); master_points+=t_data.get("master_points",[]); all_topics.append(t_data); time.sleep(8)
                print(f"    Generating exam questions...")
                meta_prompt=NOTES_META_PROMPT.format(subject_code=subject_code,subject_name=subject_name,unit_num=unit_num,unit_title=unit_title,unit_syllabus=unit_syllabus[:500])
                meta_data=call_gemini(client,meta_prompt); exam_questions=meta_data.get("exam_questions",{}); time.sleep(8)
                verified_data={"unit_title":unit_title,"ltpc":"3 0 0 3","topics":all_topics,"master_points":master_points,"exam_questions":exam_questions}
                verified_path=output_folder/f"{abbr} {subject_code}_Unit{unit_num}_VerifiedNotes.pdf"
                build_verified_notes_pdf(str(verified_path),subject_code,subject_name,unit_num,subject_type,verified_data,styles)
            except Exception as e: print(f"  Error Unit {unit_num} Verified Notes: {e}"); failures.append(f"Error Unit {unit_num} Verified Notes: {e}")
            time.sleep(10)

    print("\n"+"="*62)
    if failures:
        print(f"  FINISHED WITH {len(failures)} FAILURE(S):")
        for f in failures: print(f"    - {f}")
    else:
        print("  ALL PDFs GENERATED SUCCESSFULLY!")
    print(f"  Location: {output_folder}")
    print("\n  Files generated:")
    for f in sorted(output_folder.glob("*.pdf")): print(f"    - {f.name}")
    print("="*62+"\n")
    if failures: sys.exit(1)

if __name__=="__main__":
    main()
