# SAS Core: Degree Navigator → CoursePilot mapping (for review)

Source: `data/programs/rutgers/nb-undergrad/2026-2027/dn-mapping/sas-core.json`.
It covers DN subject "The SAS Core Curriculum (NB)", DN version "Summer 2019".

Every entry is `pending_review`: it is used, and shown with the label
**"Mapping not yet reviewed"**. When you have checked the table, say so. Every
entry then becomes `reviewed` in one commit, and the label goes away.

## Requirements (6)

| DN requirement (exact printed heading) | CoursePilot node | Review status |
|---|---|---|
| Requirement R1 : Contemporary Challenges [CC] | `CORE_CC` Contemporary Challenges | pending_review |
| Requirement R2 : Areas of Inquiry: Natural Sciences [NS] | `CORE_NS` Natural Sciences | pending_review |
| Requirement R3 : Areas of Inquiry: Social [SCL] and Historical [HST] Analysis | `CORE_HSA` Historical and Social Analysis | pending_review |
| Requirement R4 : Areas of Inquiry: Arts and Humanities [AHo], [AHp], [AHq], [AHr] | `CORE_AH` Arts and the Humanities | pending_review |
| Requirement R5 : Cognitive Skills and Processes: Writing and Communication [WC], [WCr], [WCd] | `CORE_WC` Writing and Communication | pending_review |
| Requirement R6 : Cognitive Skills and Processes: Quantitative and Formal Reasoning [QQ], [QR] | `CORE_QFR` Quantitative and Formal Reasoning | pending_review |

## Goal codes (14)

| DN code | Goal title | CoursePilot node | Role | Review status |
|---|---|---|---|---|
| CCO | Our Common Future | `CORE_CCO` Our Common Future | node | pending_review |
| CCD | Diversities and Social Inequalities | `CORE_CCD` Diversities and Social Inequalities | node | pending_review |
| NS | Natural Sciences | unmapped: R2 carries `CORE_NS` | - | unmapped |
| HST | Historical Analysis | `CORE_HST` Historical Analysis | node | pending_review |
| SCL | Social Analysis | `CORE_SCL` Social Analysis | node | pending_review |
| WC | College Writing | `CORE_COLLEGE_WRITING` College Writing | node | pending_review |
| WCr | Writing and Communication, Revision | `CORE_WCR` Revision-Based Writing | node | pending_review |
| WCd | Writing and Communication in a Discipline | `CORE_WCD` Discipline-Based Writing | node | pending_review |
| QQ | Quantitative Information | `CORE_QQ` Quantitative Information | node | pending_review |
| QR | Mathematical or Formal Reasoning | `CORE_QR` Mathematical or Formal Reasoning | node | pending_review |
| AHo | Philosophical and Theoretical Issues | `CORE_AH` Arts and the Humanities | member_goal | pending_review |
| AHp | Arts and Literatures | `CORE_AH` Arts and the Humanities | member_goal | pending_review |
| AHq | Nature of Languages | `CORE_AH` Arts and the Humanities | member_goal | pending_review |
| AHr | Critical Creative Expression | `CORE_AH` Arts and the Humanities | member_goal | pending_review |

## How the roles work

- **node:** this DN row's status is the CoursePilot node's status.
- **member_goal:** one of several DN goals of a single node. `CORE_AH` is
  "2 courses meeting 2 of AHo/AHp/AHq/AHr". These rows are mapped and shown,
  and never `unmapped`. They never decide the node: requirement R4 does.
  Reconciliation labels them `member_goal`.
- **NS is unmapped on purpose.** DN's NS goal set lists only the courses
  counted so far, so "a course is listed" does not mean NS is met. R2 carries
  `CORE_NS`'s status.
