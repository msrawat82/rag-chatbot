#!/usr/bin/env python3
"""Generate 10 synthetic GlobalOmTech policy PDFs (100 pages each) for testing the RAG system.

The documents are fictional. Each one uses a different layout (numbered headings, legal "Article"
style, single-font plain text, unnumbered headings) so the chunker is tested on varied structures.
The text is generated from templates with fixed random seeds, so every run produces the same PDFs.

    python tools/generate_globalomtech_policies.py              # writes into data/<category>/
    python tools/generate_globalomtech_policies.py --out other/dir --pages 100
"""

import argparse
import random
import sys
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, PageBreak, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy_specs import POLICIES, Policy  # noqa: E402

COMPANY = "GlobalOmTech"
OFFICES = ["Bengaluru", "London", "New York", "Singapore", "Frankfurt", "Toronto", "Sydney", "Dubai"]
PERSONAS = ["software engineer", "project manager", "sales director", "finance analyst", "HR coordinator",
            "data scientist", "support specialist", "procurement lead", "solutions architect", "marketing manager"]
FREQS = ["once a month", "once a quarter", "every six months", "once a year"]
ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii"]
CHAPTER_ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII"]

# (statement, FAQ question). Each section uses seven of these with its own numbers, roles and systems.
REQUIREMENTS = [
    ("The {role} must review {t} at least {freq} and record the outcome in {system}.",
     "How often is {t} reviewed?"),
    ("Any {t} matter with a financial impact above USD {amt} must be approved in writing by the {role2} "
     "before a commitment is made.",
     "Who approves {t} matters above USD {amt}?"),
    ("Requests relating to {t} must be raised in {system} at least {adv} business days before they take effect.",
     "How much notice is needed for a request about {t}?"),
    ("Records relating to {t} must be kept for {years} years and then securely deleted, unless a legal hold applies.",
     "How long are records about {t} kept?"),
    ("The {role} must acknowledge questions about {t} within {resp} business days and give a full answer within "
     "{resp2} business days.",
     "How quickly are questions about {t} answered?"),
    ("Everyone with responsibilities for {t} must complete the related training within {train} days of starting "
     "the role and refresh it every {refresh} months.",
     "When must training on {t} be completed?"),
    ("Exceptions to the {t} requirements may be granted only by the {role2}, for no longer than {months} months, "
     "and must be logged in {system}.",
     "Who can approve an exception to the {t} requirements?"),
    ("If an issue with {t} is not resolved within {esc} business days, it must be escalated to the {role2}.",
     "When is an issue with {t} escalated?"),
    ("Any breach of the {t} requirements must be reported to the {role} within {hours} hours of being identified.",
     "How quickly must a breach of the {t} requirements be reported?"),
    ("Every decision about {t} must be documented in {system}, including the reason, the date and the name of the "
     "approver.",
     "Where are decisions about {t} recorded?"),
    ("The compliance target for {t} is {pct}%, measured {freq2} and reported to the {role2}.",
     "What is the compliance target for {t}?"),
]

PURPOSE = [
    "This section explains how {co} manages {t}. It applies to {audience} and supports our commitment to {value}.",
    "{co} expects {t} to be handled the same way in every office and business unit. This section sets the minimum "
    "standard; local procedures may add requirements but may not lower them.",
    "The aim of this section is to make {t} predictable, fair and auditable. It sets out the responsibilities of "
    "{audience} and the controls that the {owner} relies on.",
    "{T} is an area where mistakes can cause financial, legal or reputational harm. This section describes what "
    "must happen, who is responsible and where decisions are recorded.",
    "This section describes the {co} standard for {t}. It reflects our commitment to {value} and applies in all "
    "countries where {co} operates, subject to local law.",
]
CONTEXT = [
    "Within the wider topic of {chapter_lc}, {t} is closely linked to {other}. Read both sections together when "
    "planning work.",
    "This section should be read with {other_ref} ({other}), because decisions about {t} often affect {other} too.",
    "The requirements below were updated in version {ver} after feedback from the {office} and {office2} offices, "
    "to make {t} simpler to follow.",
    "Questions about {t} are among the most common queries received by the {role}. The guidance below answers the "
    "most frequent ones.",
]
STEPS = [
    ("Initiate", "The requester records the {t} request in {system} and attaches any supporting documents."),
    ("Assess", "The {role} checks the request against this policy and, where relevant, against local law in the "
               "country concerned."),
    ("Approve", "Requests within delegated authority are approved by the {role}; others are referred to the "
                "{role2} with a recommendation."),
    ("Implement", "Once approved, the {t} change is put in place and the requester is told the outcome in writing."),
    ("Record and review", "{System} keeps the full history. The {role} samples completed {t} cases {freq} to "
                          "confirm the process was followed."),
]
GUIDANCE = [
    "Practical guidance: if you are unsure whether something counts as {t}, treat it as if it does and ask the "
    "{role} before acting. It is always easier to answer a question early than to correct a mistake later.",
    "Practical guidance: keep supporting evidence for {t} with the request in {system}, not in personal email or "
    "local folders. Auditors and reviewers rely on {system} as the single source of truth.",
    "Practical guidance: where local law in a country is stricter than this section, local law applies. The "
    "{role2} keeps a list of known local variations for {t}.",
    "Practical guidance: plan ahead. Most delays with {t} happen because requests are raised late or without the "
    "information the {role} needs to decide.",
]
GUIDANCE += [
    "Practical guidance: when {t} affects colleagues in more than one country, agree with the {role2} which "
    "country's rules apply before you start.",
    "Practical guidance: new joiners often ask about {t}. Managers should cover it during the first month and point "
    "colleagues to this section.",
]
CONTROLS = [
    "Preventive control: {system} does not allow a {t} request to be approved by the person who raised it.",
    "Detective control: the {role2} receives a report {freq2} listing {t} cases approved outside normal timelines.",
    "Preventive control: mandatory fields in {system} ensure every {t} request includes a business reason and "
    "supporting evidence.",
    "Detective control: Internal Audit tests a sample of {t} decisions every year and reports the findings to the "
    "{approver}.",
    "Corrective control: where a {t} decision is found to be wrong, the {role} corrects it within {esc} business days "
    "and informs the people affected.",
    "Preventive control: access to {t} records in {system} is limited to the {role}, the {role2} and authorised "
    "auditors.",
]
RECORDS = (
    "Records: {system} is the system of record for {t}. Records must be accurate and complete and are kept for "
    "{years} years. Personal data in these records is handled under the GlobalOmTech Data Privacy and Protection "
    "Policy."
)
NONCOMPLIANCE = [
    "Failure to follow the {t} requirements may lead to the request being rejected, costs not being reimbursed, "
    "or disciplinary action under the Employee Code of Conduct.",
    "Repeated non-compliance with the {t} requirements is reported to the {role2} and considered in performance "
    "reviews.",
    "Deliberate breaches of the {t} requirements are treated as serious misconduct and may be reported to "
    "regulators where the law requires it.",
]
EXAMPLE = (
    "Example: a {persona} in the {office} office needs help with {t}. They raise the request in {system} {adv} "
    "business days in advance. The {role} reviews it, confirms that it meets this policy and records the decision, "
    "and the {persona} receives a written answer within {resp2} business days."
)
SCENARIO = (
    "A {persona} based in the {office} office asks about {t}. {statement} In this case the {role} checks the details, "
    "confirms the outcome in writing and updates {system}. Because the matter was raised early and with the right "
    "information, it is closed without escalation. Reference: {ref}."
)


def lc(text):
    """Lower-case the first letter unless the word is an acronym (ESG, DNS, VPN...)."""
    return text if len(text) > 1 and text[1].isupper() else text[0].lower() + text[1:]


def cap(text):
    return text[0].upper() + text[1:]


# ─── Styles ────────────────────────────────────────────────────────────────────

def make_styles(style):
    serif = style == "article"
    body_font, bold_font = ("Times-Roman", "Times-Bold") if serif else ("Helvetica", "Helvetica-Bold")
    size = 10.5 if serif else 10
    body = ParagraphStyle("body", fontName=body_font, fontSize=size, leading=size * 1.38, spaceAfter=6)
    s = {"body": body,
         "item": ParagraphStyle("item", parent=body, leftIndent=18, firstLineIndent=-14, spaceAfter=3),
         "lead": ParagraphStyle("lead", parent=body, fontName=bold_font, spaceBefore=4, spaceAfter=3),
         "cell": ParagraphStyle("cell", parent=body, fontSize=size - 1, leading=(size - 1) * 1.3, spaceAfter=0),
         "caption": ParagraphStyle("caption", parent=body, fontName=bold_font, fontSize=size - 0.5, spaceBefore=6),
         "cover_title": ParagraphStyle("ct", fontName="Helvetica-Bold", fontSize=26, leading=32, alignment=TA_CENTER,
                                       textColor=colors.HexColor("#12355b")),
         "cover_sub": ParagraphStyle("cs", fontName="Helvetica", fontSize=13, leading=18, alignment=TA_CENTER,
                                     textColor=colors.HexColor("#444444")),
         "control_head": ParagraphStyle("chd", fontName=bold_font, fontSize=13, leading=17, spaceAfter=8)}
    if style == "numbered":
        s["h1"] = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=19, spaceBefore=14,
                                 spaceAfter=8, textColor=colors.HexColor("#12355b"))
        s["h2"] = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=10,
                                 spaceAfter=4)
    elif style == "article":
        s["h1"] = ParagraphStyle("h1", fontName="Times-Bold", fontSize=14, leading=18, spaceBefore=6, spaceAfter=10,
                                 alignment=TA_CENTER)
        s["h2"] = ParagraphStyle("h2", fontName="Times-Bold", fontSize=11, leading=15, spaceBefore=10, spaceAfter=4)
    elif style == "unnumbered":
        s["h1"] = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=16, leading=20, spaceBefore=14,
                                 spaceAfter=8, textColor=colors.HexColor("#0b6e6e"))
        s["h2"] = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=16, spaceBefore=10,
                                 spaceAfter=4, textColor=colors.HexColor("#0b6e6e"))
    else:  # plain: one font and size for everything, like a text export
        s["h1"] = ParagraphStyle("h1", parent=body, spaceBefore=16, spaceAfter=6)
        s["h2"] = ParagraphStyle("h2", parent=body, spaceBefore=10, spaceAfter=3)
        for k in ("lead", "caption", "cell", "control_head"):
            s[k] = ParagraphStyle(k, parent=body, spaceBefore=4, spaceAfter=3)
    s["faq"] = ParagraphStyle("faq", parent=s["lead"] if style != "plain" else body, spaceBefore=8)
    return s


# ─── Document template with header, footer and table of contents ──────────────

class PolicyDoc(BaseDocTemplate):
    def __init__(self, path, policy: Policy):
        super().__init__(path, pagesize=LETTER, leftMargin=2.2 * cm, rightMargin=2.2 * cm, topMargin=2.4 * cm,
                         bottomMargin=2.2 * cm, title=f"{COMPANY} {policy.title}", author=COMPANY,
                         subject=f"{policy.code} version {policy.version}")
        self.policy = policy
        self.last_page = 0
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates([PageTemplate("main", [frame], onPage=self._decorate)])

    def _decorate(self, canv, doc):
        self.last_page = doc.page
        if doc.page == 1:
            return
        p = self.policy
        canv.saveState()
        canv.setFont("Helvetica", 8)
        canv.setFillColor(colors.HexColor("#666666"))
        w, h = LETTER
        if p.style == "article":
            canv.drawCentredString(w / 2, h - 1.4 * cm, f"{COMPANY.upper()} - {p.title.upper()} - CONFIDENTIAL")
            canv.drawCentredString(w / 2, 1.2 * cm, f"Page {doc.page}")
        elif p.style == "plain":
            canv.drawString(2.2 * cm, 1.2 * cm, f"{p.code} v{p.version}")
            canv.drawRightString(w - 2.2 * cm, 1.2 * cm, f"- {doc.page} -")
        else:
            canv.drawString(2.2 * cm, h - 1.4 * cm, f"{COMPANY} | {p.title} | Version {p.version}")
            canv.drawRightString(w - 2.2 * cm, h - 1.4 * cm, "Internal")
            canv.line(2.2 * cm, h - 1.55 * cm, w - 2.2 * cm, h - 1.55 * cm)
            canv.drawString(2.2 * cm, 1.2 * cm, f"{p.code} | Effective {p.effective}")
            canv.drawRightString(w - 2.2 * cm, 1.2 * cm, f"Page {doc.page}")
        canv.restoreState()

    def afterFlowable(self, flowable):
        level = getattr(flowable, "toc_level", None)
        if level is not None:
            self.notify("TOCEntry", (level, flowable.toc_text, self.page))


def heading(text, style, level, toc_text=None):
    p = Paragraph(escape(text), style)
    p.toc_level, p.toc_text = level, toc_text or text
    return p


def make_table(header, rows, s, grid=True):
    data = [[Paragraph(f"<b>{escape(h)}</b>", s["cell"]) for h in header]]
    data += [[Paragraph(escape(c), s["cell"]) for c in r] for r in rows]
    t = Table(data, repeatRows=1, hAlign="LEFT")
    cmds = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if grid:
        cmds += [("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
                 ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5"))]
    t.setStyle(TableStyle(cmds))
    return t


# ─── Story ─────────────────────────────────────────────────────────────────────

class Numbering:
    def __init__(self, style):
        self.style = style

    def chapter(self, n, title):
        if self.style == "article":
            return f"ARTICLE {CHAPTER_ROMAN[n - 1]} - {title.upper()}"
        if self.style == "plain":
            return f"{n}. {title.upper()}"
        if self.style == "unnumbered":
            return title
        return f"{n}. {title}"

    def section(self, n, m, title):
        if self.style == "article":
            return f"Section {n}.{m:02d} - {title}"
        if self.style == "unnumbered":
            return title
        return f"{n}.{m} {title}"

    def ref(self, n, m, title):
        if self.style == "article":
            return f"Section {n}.{m:02d}"
        if self.style == "unnumbered":
            return f'"{title}"'
        return f"section {n}.{m}"

    def item(self, i):
        return {"article": f"({ROMAN[i]})", "plain": "-", "unnumbered": "•"}.get(self.style, f"{chr(97 + i)})")


def section_context(p: Policy, chapter, title, other, other_ref, rng):
    role = rng.choice(p.roles[:3])
    role2 = rng.choice([r for r in p.roles[2:] if r != role])
    system = rng.choice(p.systems)
    office, office2 = rng.sample(OFFICES, 2)
    resp = rng.choice([1, 2, 3])
    return dict(co=COMPANY, t=lc(title), T=title, chapter_lc=lc(chapter), role=role, role2=role2, system=system,
                System=cap(system), freq=rng.choice(FREQS), freq2=rng.choice(FREQS),
                amt=f"{rng.choice([2500, 5000, 10000, 25000, 50000, 100000]):,}",
                adv=rng.choice([3, 5, 10, 15, 20]), years=rng.choice([3, 5, 6, 7, 10]), resp=resp,
                resp2=resp + rng.choice([3, 5, 7]), train=rng.choice([14, 30, 45, 60, 90]),
                refresh=rng.choice([12, 24]), months=rng.choice([3, 6, 12]), esc=rng.choice([3, 5, 10]),
                hours=rng.choice([4, 24, 48, 72]), pct=rng.choice([95, 97, 98, 99, 100]),
                office=office, office2=office2, persona=rng.choice(PERSONAS), value=rng.choice(p.values),
                audience=p.audience, owner=p.owner, approver=p.approver, ver=p.version, other=lc(other), other_ref=other_ref)


def build_section(p, num, s, n, m, chapter, title, other, other_ref):
    rng = random.Random(zlib.crc32(f"{p.code}|{title}".encode()))
    ctx = section_context(p, chapter, title, other, other_ref, rng)
    f = lambda tpl: escape(tpl.format(**ctx))  # noqa: E731
    ref = num.ref(n, m, title)

    story = [heading(num.section(n, m, title), s["h2"], 1)]
    story.append(Paragraph(f(rng.choice(PURPOSE)), s["body"]))
    if rng.random() < 0.75:
        story.append(Paragraph(f(rng.choice(CONTEXT)), s["body"]))

    facts = []
    reqs = rng.sample(REQUIREMENTS, 7)
    if title in p.tables:  # avoid generic money thresholds where a table gives the real numbers
        reqs = [r for r in reqs if "USD {amt}" not in r[0]]
    story.append(Paragraph("Requirements" + ("" if p.style == "plain" else ":"), s["lead"]))
    for i, (stmt, q) in enumerate(reqs):
        story.append(Paragraph(f"{num.item(i)} {f(stmt)}", s["item"]))
        facts.append((stmt.format(**ctx), q.format(**ctx), ref, ctx))

    if title in p.tables:
        caption, header, rows = p.tables[title]
        story.append(Paragraph(escape(f"Table: {caption}"), s["caption"]))
        story.append(make_table(header, rows, s, grid=p.style != "plain"))
        story.append(Spacer(1, 6))

    if True:
        story.append(Paragraph("Procedure" + ("" if p.style == "plain" else ":"), s["lead"]))
        steps = STEPS if rng.random() < 0.5 else STEPS[:2] + STEPS[3:]
        for i, (label, text) in enumerate(steps, 1):
            story.append(Paragraph(f"Step {i} - {label}. {f(text)}", s["item"]))

    story.append(Paragraph("Key controls" + ("" if p.style == "plain" else ":"), s["lead"]))
    for i, c in enumerate(rng.sample(CONTROLS, 3)):
        story.append(Paragraph(f"{num.item(i)} {f(c)}", s["item"]))
    for g in rng.sample(GUIDANCE, 2):
        story.append(Paragraph(f(g), s["body"]))
    story.append(Paragraph(f(RECORDS), s["body"]))
    story.append(Paragraph(f("Responsibilities: the requester provides complete and accurate information; the "
                             "{role} owns day-to-day decisions on {t}; the {role2} approves exceptions and resolves "
                             "escalations; and the {owner} maintains this section."), s["body"]))
    if rng.random() < 0.6:
        story.append(Paragraph(f(rng.choice(NONCOMPLIANCE)), s["body"]))
    if rng.random() < 0.8:
        story.append(Paragraph(f(EXAMPLE), s["body"]))
    return story, facts


def build_story(p: Policy, n_scenarios):
    s = make_styles(p.style)
    num = Numbering(p.style)
    story = []

    # Cover page
    story += [Spacer(1, 5 * cm), Paragraph(COMPANY, s["cover_sub"]), Spacer(1, 0.6 * cm),
              Paragraph(escape(p.title), s["cover_title"]), Spacer(1, 0.8 * cm),
              Paragraph(f"Version {p.version} | Effective {p.effective}", s["cover_sub"]),
              Paragraph(f"Document ID {p.code} | Classification: Internal", s["cover_sub"]),
              Spacer(1, 6 * cm),
              Paragraph(f"Policy owner: {escape(p.owner)}<br/>Approved by: {escape(p.approver)}", s["cover_sub"]),
              PageBreak()]

    # Document control and version history
    major, minor = (int(x) for x in p.version.split("."))
    history = [[p.version, p.effective, "Annual review; requirements and examples updated", p.owner]]
    if minor:
        history.append([f"{major}.{minor - 1}", "Previous year", "Clarified approvals and record keeping", p.owner])
    history.append([f"{major}.0" if minor else f"{max(major - 1, 1)}.0", "Earlier",
                    "Major revision" if minor else "Previous major version", p.approver])
    story += [Paragraph("Document Control", s["control_head"]),
              make_table(["Field", "Value"], [["Document ID", p.code], ["Version", p.version],
                                              ["Effective date", p.effective], ["Policy owner", p.owner],
                                              ["Approved by", p.approver], ["Review cycle", "Every 12 months"],
                                              ["Classification", "Internal"]], s),
              Spacer(1, 12), Paragraph("Version History", s["control_head"]),
              make_table(["Version", "Date", "Summary of changes", "Approved by"], history, s), PageBreak()]

    # Table of contents
    toc = TableOfContents()
    font = "Times-Roman" if p.style == "article" else "Helvetica"
    toc.levelStyles = [ParagraphStyle("toc0", fontName=font, fontSize=10, leading=13, leftIndent=0),
                       ParagraphStyle("toc1", fontName=font, fontSize=9, leading=11.5, leftIndent=16)]
    story += [Paragraph("Table of Contents", s["control_head"]), toc, PageBreak()]

    # Chapter 1: introduction
    story.append(heading(num.chapter(1, "Introduction"), s["h1"], 0))
    intro = [("Purpose", p.purpose),
             ("Scope", f"This policy applies to {p.audience}, in every country where {COMPANY} operates, including "
                       f"our offices in {', '.join(OFFICES[:-1])} and {OFFICES[-1]}. Where local law sets a higher "
                       "standard, local law applies."),
             ("Policy governance", f"The {p.owner} owns this policy and reviews it every 12 months. Changes are "
                                   f"approved by the {p.approver}. Questions about this policy should be sent to the "
                                   f"{p.roles[1]}.")]
    for m, (t, text) in enumerate(intro, 1):
        story += [heading(num.section(1, m, t), s["h2"], 1), Paragraph(escape(text), s["body"])]
    story.append(heading(num.section(1, 4, "Definitions"), s["h2"], 1))
    if p.style in ("numbered", "unnumbered"):
        story.append(make_table(["Term", "Meaning"], p.glossary, s))
    else:
        for term, meaning in p.glossary:
            story.append(Paragraph(f"<b>\"{escape(term)}\"</b> means {escape(lc(meaning))}", s["body"]))
    duties = ["Day-to-day decisions and first point of contact", "Operates the process and keeps records",
              "Advice, assurance and escalations", "Approves exceptions and owns the policy area",
              "Supports with specialist input", "Supports with specialist input"]
    story += [heading(num.section(1, 5, "Roles and responsibilities"), s["h2"], 1),
              make_table(["Role", "Responsibility"], [[cap(r), d] for r, d in zip(p.roles, duties)], s,
                         grid=p.style != "plain")]

    # Main chapters
    all_facts = []
    for ci, (chapter, sections) in enumerate(p.chapters, start=2):
        story.append(PageBreak() if p.style == "article" else CondPageBreak(8 * cm))
        story.append(heading(num.chapter(ci, chapter), s["h1"], 0))
        story.append(Paragraph(escape(f"This chapter covers {', '.join(lc(x) for x in sections[:-1])} and "
                                      f"{lc(sections[-1])}. It forms part of the {COMPANY} {p.title} and should be "
                                      "read together with the Introduction."), s["body"]))
        for si, title in enumerate(sections, start=1):
            oi = si % len(sections)  # the next section in the chapter
            other_ref = num.ref(ci, oi + 1, sections[oi])
            sec, facts = build_section(p, num, s, ci, si, chapter, title, sections[oi], other_ref)
            story += sec
            all_facts += facts

    # FAQ chapter, built from the requirements above so answers always match the sections
    rng = random.Random(zlib.crc32(p.code.encode()))
    faq_n = len(p.chapters) + 2
    story.append(CondPageBreak(8 * cm))
    story.append(heading(num.chapter(faq_n, "Frequently Asked Questions"), s["h1"], 0))
    story.append(Paragraph("The answers below summarise the policy. The referenced section is authoritative.",
                           s["body"]))
    for i, (stmt, q, ref, _) in enumerate(rng.sample(all_facts, 40), 1):
        label = f"FAQ-{i:02d}: " if p.style in ("numbered", "unnumbered") else "Q: "
        story.append(Paragraph(escape(label + cap(q)), s["faq"]))
        story.append(Paragraph(escape(f"{stmt} See {ref}."), s["body"]))

    # Appendix of worked scenarios: sized so the document has exactly the requested number of pages
    story.append(PageBreak())
    story.append(heading("Appendix A - Worked Scenarios", s["h1"], 0))
    story.append(Paragraph("These scenarios show how the policy applies in everyday situations. They are "
                           "illustrations, not additional rules.", s["body"]))
    for i in range(n_scenarios):
        stmt, _, ref, ctx = all_facts[(i * 7) % len(all_facts)]
        c = dict(ctx, persona=PERSONAS[i % len(PERSONAS)], office=OFFICES[(i * 3) % len(OFFICES)])
        story.append(Paragraph(escape(f"Scenario {i + 1}: {c['T']}"), s["lead"]))
        story.append(Paragraph(escape(SCENARIO.format(statement=stmt, ref=ref, **c)), s["body"]))
    return story


def build(p, path, n_scenarios):
    doc = PolicyDoc(str(path), p)
    doc.multiBuild(build_story(p, n_scenarios))
    return doc.last_page


def generate(p, out_dir, target):
    folder = out_dir / p.folder
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / p.filename
    n, seen = 40, {}
    for _ in range(30):
        pages = build(p, path, n)
        seen[n] = pages
        if pages == target:
            return path, pages
        step = (target - pages) * 4
        nxt = max(0, n + step)
        if nxt in seen or abs(step) <= 4:
            nxt = n + (1 if pages < target else -1)
        if nxt < 0 or nxt in seen:
            break
        n = nxt
    return path, pages


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data"))
    ap.add_argument("--pages", type=int, default=100)
    args = ap.parse_args()
    out = Path(args.out)
    for p in POLICIES:
        path, pages = generate(p, out, args.pages)
        print(f"{pages:4d} pages  {path.relative_to(out.parent) if out.parent in path.parents else path}")


if __name__ == "__main__":
    main()
