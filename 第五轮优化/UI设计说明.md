# 第五轮UI设计说明

状态：本轮59项UI资产及采用关系见《页面设计清单.json》和《UI设计索引.html》；首页采用已确认方案，实时/视频按现有前端局部增强。当前处于代码实施和运行验收，实际按钮、字段及异常状态以《开发详细文档》第17—19节和《开发步骤》验证记录为准，不把旧设计阶段段落当成当前开发状态。

## 当前信息架构（2026年11月10日，按用户最新合并要求）

设计依据：一级导航按用户的主要任务分组，技术能力、输入方式和内容主题不能不加区分地各占一项。首页、实时分析、视频分析是用户明确要求保留的入口；计划与历史共同服务本人训练，知识与饮食共同服务查方法、看依据和获取建议。

| 一级导航 | 用户要做什么 | 二级内容 |
| --- | --- | --- |
| 首页 | 了解产品并快速开始分析 | 产品介绍、动作演示、实时/视频两个明确操作 |
| 实时分析 | 训练时用摄像头观察动作 | 原实时流程、取景、反馈、语音 |
| 视频分析 | 上传已有视频，定位并回看问题 | 上传、任务进度、问题时间段、结果 |
| 我的训练 | 安排自己的训练并查看执行结果 | 训练计划／训练记录／训练回顾 |
| 健身指南 | 查动作方法、饮食知识与建议依据 | 动作知识／饮食建议 |

“我的训练”默认训练计划，今日安排、执行进度与纠正待办在此组织；记录子页集中管理历史、报告和前后对比；回顾解释指定阶段的真实变化。纠正任务从计划与相关记录直达，不再单列一级入口。

“健身指南”默认动作知识，包含主题检索、带来源回答和动作详情；饮食建议为同一模块内可直接访问的二级页面，包含摄入参考与一般饮食问答。两者使用相同搜索/来源/引用组件与视觉规则，但保留各自数据与隐私边界；饮食不能因合并页面就把身体资料自动带入公共知识问答。

AI教练是页面内咨询能力和保留的通用聊天入口，不再承担全部业务导航。用户仍能直接打开原/coach自由问答。首页不列最近训练，不复制完整计划、历史图表与周回顾；只放清楚的核心分析入口及“我的训练”“健身指南”两组轻量直达入口。

桌面和手机均使用这五项一级导航，手机底栏每项保留文字、触控区域与安全区。指南二级入口默认可见，不隐藏在聊天折叠面板；“饮食建议”依然能由首页或模块内直接进入，合并不等于降低可发现性。登录默认首页，原安全returnTo与所有已有路由保持兼容。

此前五项“我的训练/动作分析/记录/知识/饮食”与六项“首页/实时/视频/我的训练/知识/饮食”导航均已作废，不继续生成旧结构图稿。当前页面方案统一按本节整理。


### 首页最新保留边界（2026年11月10日）

用户明确要求：首页按照原有页面，第一屏不用重做；向下滚动后的内容区可以调整，首屏允许增加“问AI教练”按钮。本约定优先于此前左右分栏hero生图方案；已生成的01_教练总览.png不是采用的首页首屏。

首屏以现有`fitness_app/templates/home.html`和`static/home_landing.css`为准：居中标题“看清每个动作，练好下一组。”、原副标题“实时看见姿态，找到下一次进步。”、居中“开始实时分析／上传训练视频”、下方宽幅演示播放器及原轮播、问题提示和全屏操作。不得改成左文右图，不换人物或演示片，不重做原背景装饰，不缩小播放器给新卡片让位。顶栏按已确定五项导航改名称与归属，其品牌、尺寸、视觉继续沿用原站。

唯一首屏内容增加：在原两个分析按钮之后、仍属原操作区域放轻量“问AI教练”入口，视觉低于主操作。桌面有空间时三项同组；宽度不足时AI入口独占一行居中，不缩小原两按钮。使用文本加项目既有图标，不新建固定悬浮球。指向原`/coach`通用问答，不自动发送、附图、启动摄像头或读取私人记录；未登录沿用原登录及returnTo，不新增接口或访问授权。

首页下滑区当前布局（2026年11月10日）：用户明确否定选择切换导航，要求完整设计一版首页。此前切换舞台、双空卡和堆叠预览方案全部不采用。首页为正常纵向滚动长页：原首屏（居中标题、原副标题、原分析按钮与原演示视频）保留，补问AI教练轻量入口；下方依次为动作调整与复测、训练计划与进展、健身知识与饮食指导，最后简洁行动收束与页脚。各段自然衔接，一段只突出一个主题和一个主要入口，不用Tab、分段选择、轮播、滚动锁定或隐藏内容承载这些介绍。顶栏五项主导航继续有效，模块内部业务导航不受首页禁止切换的要求影响。

交付完整首页长图`UI图/首页完整UI.png`；首屏由原截图在本地保留合成，下滑区另行生成，人物原片不发送模型。

## 工具与参考

本机已授权原生imagegen兼容入口，主窗口身份不改。首次参考图请求返回images/edits接口404，改为无参考图文字生成通道成功。每张一个独立任务，顺序生成，同一公共风格提示词，不附历史整套图。用户已认可重做旧3张，使用replace仅覆盖这3张。全部完成后用本地review联系表查看，原PNG不改写。

## 公共提示词

```text
Use case: ui-mockup. Generate ONE premium high-fidelity Chinese desktop WEB product screenshot, 16:10 landscape around1600x1000, full bleed, no browser chrome, no device frame, no collage. Product: 知练同行 intelligent fitness coaching. No reference image attached. Preserve established warm ivory #FFF9F1 page, cream #FFFCF8 surfaces, dark brown #402517 ink, muted warm brown #78675B secondary, burnt orange #B9431C primary controls and pale apricot #FFF0DF highlights. Elegant professional warm fitness product, crisp readable Chinese system sans, generous functional whitespace, subtle16px rounded surfaces, delicate warm borders, almost no shadow. No gradients, no giant hero illustration, no equal-card wall or card nesting. Header with the established orange running-person symbol and 知练同行 at left. EXACT global navigation labels in this exact order: 首页 / 实时分析 / 视频分析 / 我的训练 / 健身指南. FIVE text links. Training plans and training records are inside 我的训练. Exercise knowledge and nutrition advice are inside 健身指南. Retain 首页 and keep 实时分析 and 视频分析 as distinct primary links. No 动作分析 primary grouping, no separate knowledge or nutrition primary tabs. Right small anonymous avatar. Main content around1280px wide, one dominant task region, restrained supporting lists. AI consultation is an in-context button 问AI教练, never a global navigation destination. Body16px, labels14px, page title30px. Small legible label UI方案 · 示例数据 at top right. All data is fictional design examples. No health score, total posture score, fake achievements, calories burned, payment, notifications, medical claims, implementation jargon, RAG/API labels or hidden reasoning. Prioritize consistent header, clear typography and user tasks. 
```

## 01_教练总览.png（旧方案，不采用）

此前生成的左右分栏首页首屏已被用户否决，不继续使用其提示词或图面。原首屏采用现有代码，只新增问AI教练入口。

## 02_训练计划.png

当前下一个设计页：我的训练的训练计划默认页。首页已确认，不改首页。今日为示例周二11月10日，计划周二/周四/周六训练；今日1/3个动作，本周0/3次训练，两种分母明确区分。

```text
Generate ONE mature, beautifully organized desktop page for 我的训练, TRAINING PLAN view. Exact shared header: orange running-person brand mark 知练同行 on left, five global links 首页 / 实时分析 / 视频分析 / 我的训练 / 健身指南 centered; 我的训练 active. Small anonymous avatar at right. Under header, title 我的训练 and a short quiet subtitle 安排今天的练习，记录每一步完成。. Page-level subnavigation 训练计划 / 训练记录 / 训练回顾, 训练计划 active. These are legitimate business-page tabs; no extra repeated tabs.
Use a well-balanced actual product layout about1600x1000, no browser chrome, no device mockup. Warm ivory background #FFF9F1, cream panels, brown ink #402517, muted brown #78675B, burnt orange #B9431C. Typography precise16px body,30px page title. Header72px. Main content1280px, left dominant about850px, right supporting about300px, gap24px. Avoid both card clutter and empty basic boxes. Clear shared alignments, calm refined surfaces, subtle hairlines,16px corners, restrained warm depth. Use local Chinese readable labels, no giant marketing typography or decorative gym photo.
BELOW subnavigation: a compact horizontal seven-day selector across MAIN LEFT column for week 11月9日—11月15日. Dates9/10/11/12/13/14/15 correspond 周一/周二/周三/周四/周五/周六/周日. 周二10 is selected and marked 今天; training days Tue/Thu/Sat, other days small 休息. No seven giant calendar cards. Put a quiet week navigation arrow at ends. This is a date picker, not another top-level module navigation.
DOMINANT main panel immediately below: heading 今日训练 · 上肢训练; supporting line 预计40分钟 · 已完成1/3个动作. A single thin33% horizontal task progress line, orange filled one third. One strong orange 继续训练 button near heading, not repeated across all rows. Three generous but compact exercise rows, subtle small natural equipment thumbnails (monochrome/simple cutouts): 器械推胸 · 3组×10次 · 已完成 with small check; 站姿哑铃弯举 · 3组×10次 · 待继续; 坐姿划船 · 3组×10次 · 未开始. Short action detail chevrons at row ends. No all-row primary buttons. One subtle footer link 查看动作要点 under the current exercise or next to it, not repeated boilerplate everywhere. Do not show editable reps fields here: this page overview navigates to workout execution, it is not the set recording form.
RIGHT supporting area: compact plan summary 每周三练, 第2周, quiet 编辑计划 text action. Below, one tasteful summary block 本周计划执行, 0/3次, caption 今天的训练尚未结束. Very quiet unfilled track at0%, NOT67% or33%: weekly completed SESSIONS are0/3 while today's completed ITEMS are1/3. Display 0/3 as text, no giant donut or three KPI tiles. Small next-session row 下次训练 · 周四 / 下肢训练. One quiet 问AI教练 text link at bottom, no chat pane, no auto-generated conversation.
Below main panel ONE slim context reminder with title 本次关注：推胸时的躯干稳定 and secondary 查看纠正任务 →, no full task list or history. This reminder is example, no diagnosed injury. At lower page margin small UI方案 · 示例数据. Keep all important content visible in screenshot, reasonable whitespace, enough density without being crowded. No 最近训练, no training history table, no nutrition card, no knowledge search, no line charts, no fabricated health score, no streaks or calorie consumption. Design must feel like a professional usable training product, with one clear task hierarchy and intentional alignment. Exact examples are not a medical or training prescription.
```

## 03_知识问答.png

```text
Global 健身指南 active. Local tabs 动作知识 / 饮食建议, 动作知识 active. Knowledge search and question-answer are one continuous task, not extra primary tabs. Title 有依据的训练知识. Search field 器械推胸回程怎么控制？ with orange 查询 button. Small topic selector 动作知识 / 饮食知识 / 增肌与恢复. Answer reading state: quiet completed status 已检索适用资料, large readable answer heading 推回后，保持可控制的回程. Three concise paragraphs with inline [1] citation and one [2] for 工程参考, no dense essays. Right light source rail 本次参考 with source ACE · Seated Chest Press, short excerpt 回程保持可控制的动作, external source link; second 项目观察规则 with explicit 工程参考 badge. Sources visible as restrained rows, not hidden panels or detached cards. Bottom 查看推胸动作详情 main related action, small 继续追问 to context AI. No training/history/plan metrics, no four-tab AI nav, no AI教练 nav label. Clear query→answer→source relationship.
```

## 04_饮食建议.png

```text
Global 健身指南 active. Local tabs 动作知识 / 饮食建议, 饮食建议 active. Within page provide clear 摄入参考 and 饮食问答 sections, not extra global navigation. Heading 饮食建议, short 了解参考范围，也看清依据. Main left adopted profile strip 已采用本人保存资料 · 修改, adult24岁,70公斤, 男, 规律运动, 久坐或轻活动, orange 估算参考范围. Two prominent but calm results next to each other: 每日蛋白质参考 95—140 克/天, visible source ISSN · 健康成年规律运动者; 每日维持热量参考 2400—3000 千卡/天, source FAO/WHO/UNU, caption 维持体重参考，不是减脂目标. Each has 计算依据 disclosure link. Right light contextual question area 饮食问题，也可以直接问 with examples 训练后怎么吃 / 蛋白质来源怎么选, short question box and 问AI教练 button. Bottom quiet 本次结果未保存 and 保存当前资料 secondary action. Values fictional but coherent. No diet tracker, food photo hero, exact meal recipes, calories burned, full AI four-tab nav or repeated training metrics.
```

## 05_纠正任务.png

```text
Global 我的训练 active. Primary local tabs 训练计划 / 训练记录 / 训练回顾, 训练计划 active. Breadcrumb 训练计划 / 纠正任务. Heading 练习一项，再验证一次. Simple filters 待练习1 / 待复测1 / 已有比较1. Dominant current task 器械推胸 · 躯干稳定 with 待练习 status, 查看原训练片段 source link and exactly3 short practice steps 检查坐姿 / 关注躯干位置 / 结束后拍摄复测, visible source ACE · Seated Chest Press, orange 开始针对性练习 and quiet 问AI教练. Other two tasks light rows: 站姿哑铃弯举 · 回程控制, 待复测, 上传复测视频; 徒手深蹲 · 幅度观察, 已有比较, 查看前后结果. Small right process 练习 → 新记录 → 同项比较. No total progress score, no recent training list, no nested cards, no duplicate tasks section.
```

## 06_前后对比.png

```text
Global 我的训练 active. Primary local tabs 训练计划 / 训练记录 / 训练回顾, 训练记录 active. Breadcrumb 训练记录 / 复测结果 / 前后对比. Heading 看见这一次调整. Two equally sized video panels side by side, same anonymous adult on chest press machine, same view; left 原训练, right 复测训练. Left slightly leaning, right upright, understated green pose keypoints on visible body. Each a small timestamp and 本地原片已匹配 caption. One shared media toolbar below, play/pause,0.5×/0.75×/1×, orange 同步回看. Explicit 手动对齐 label and 设置左侧起点 / 设置右侧起点 controls. Beneath single concise comparison strip 同项偏差 原训练3/8次 · 复测1/8次, note 仅比较本项可评估观察, not global improvement. Sources and comparability as lightweight text rows, no green correctness certificate or total score. Small contextual 问AI教练 button. No extra recent records, no four AI tabs.
```

## 07_训练回顾.png

```text
Global 我的训练 active. Primary local tabs 训练计划 / 训练记录 / 训练回顾, 训练回顾 active. Heading 这一周，练习与调整, small week selector 11月9日—11月15日. Top compact fact summary 已完成2/3次计划训练 with67% bar and small 查看计划 link; distinguish plan progress from observation changes. Dominant single line chart 器械推胸 · 侧倾观察, three dates with actual sample fractions3/8,2/8,1/8, caption 仅展示同项可比记录. Right small narrative focus 下次仍关注躯干稳定 with 查看关联记录 and 继续复测 button. Bottom 3 concise evidence rows, not another full record table. Contextual 问AI教练 for explanation. One chart only, no health score, no muscle-growth prediction, no weight-loss claim, no duplicate today's workout card.
```

## 08_今日训练_手机.png

```text
MOBILE OVERRIDE: Generate ONE portrait MOBILE WEBSITE screen,390x844 composition, no browser chrome, no device frame, no native status bar, no desktop header. Compact top dumbbell logo 知练同行 and avatar. Small local tabs 训练计划 / 训练记录 / 训练回顾, 训练计划 active; 今日训练 is the current plan task. Page title 今日训练, 上肢训练 · 预计40分钟. One task summary 已完成2/5个动作,40% horizontal progress, no extra main CTA because current recording is active below. Slim week date strip 周三 selected. Today action rows first two checked. Third selected 器械推胸 expands INLINE 目标3组×10次, 第2/3组, actual reps field 实际次数 10, optional 重量（公斤） blank, orange 记录这一组. Small 查看动作要点 and 问AI教练 text links. One remaining action row visible, natural scrolling. Fixed bottom EXACT five global labels 首页 / 实时分析 / 视频分析 / 我的训练 / 健身指南, icons plus readable text, 我的训练 active, safe-area space. No second floating CTA overlapping bottom navigation. Small UI方案 · 示例数据 label. No 最近训练, no native UI, no collage.
```


## 其余页面的旧提示已统一调整，图稿待修订

第02—08张提示已按当前五项一级导航更新；现有PNG仍为旧导航图稿，须逐张修订后才能作为采用候选。本轮先核对首页，不自动按旧图片继续开发。


## 旧首页首屏审阅结论

此前左右分栏首页图不采用；原首页居中标题、按钮与下方视频保留。已有预览不作为新首屏开发依据。下滑区另外生成，首屏问AI教练按钮以开发文档契约实施。


## 首页下滑区.png

完整连续三段内容＋结尾，不使用任何选择切换导航。首屏另用原截图本地保留。

```text
Use case: ui-mockup. Generate ONE complete continuous BELOW-THE-FOLD Chinese desktop homepage design, tall1440x2600 composition, full bleed. This image will be appended BELOW an existing first-screen hero. DO NOT draw the first-screen hero, header, main navigation, browser chrome, phone frame or collage. Product 知练同行. Warm ivory #FFF9F1, cream #FFFCF8, dark brown #402517, muted #78675B, burnt orange #B9431C, pale apricot #FFF0DF. Beautiful mature fitness product website, confident Chinese typography, purposeful whitespace, cohesive continuous background. Inspired by mature Apple Fitness+/Nike product storytelling rhythms, ORIGINAL assets and layout. ALL sections appear in natural vertical reading order, NO tabs, NO segmented selection, NO carousel, NO choose-to-reveal controls. Not a wireframe, not sparse two-icon cards, not a dashboard wall.
Create THREE polished editorial feature sections stacked vertically plus a compact closing CTA and footer. Same1200px content grid, approximately520px height per main feature, ample margins but not giant empty regions, heading36-44px, body18px, concise realistic Chinese. Alternate composition and slight ivory/cream backgrounds for rhythm; no thick enclosing border for whole page.
SECTION1: left short heading 每一次调整，都有方向。, two lines 看清问题发生在哪里，按建议练习，再用复测查看变化。, orange text link 查看动作反馈 →. Right a cohesive pair of real-photo style exercise stills, same anonymous adult and same chest-press machine, before slightly leaning vs after upright. Labels 调整前 / 调整后, small note 动作调整示意. Do not add medical claims, fake overall scores, colored skeleton overload, sources lists or extra product cards. Photography must look consistent warm natural gym scene and remain secondary to preserved original hero outside this image.
SECTION2: subtly warmer ground, LEFT one convincing desktop product preview of 我的训练, clean title 今日训练 · 上肢训练, subtle week-day strip, THREE rows 器械推胸 / 哑铃弯举 / 坐姿划船, first checked, thin progress1/3个动作, one 继续训练 button, small 功能示意. Product preview is ONE composed UI surface, no nested floating cards. RIGHT heading 计划与记录，让坚持看得见。, two lines 安排每周训练，记录每次完成，也看清下一次的重点。, orange text link 查看我的训练 →. No unrelated charts, recent-history table or repeated statistics.
SECTION3: heading 练得明白，也吃得有依据。 on left with short explanation 查动作方法，了解饮食参考。每条建议，都有可查看的依据。, orange 查看健身指南 →, quiet 了解饮食建议 link. RIGHT ONE composed guide preview, no tabs or local switch. A concise question 推胸回程怎么控制？ and short answer 回程保持平稳，按可控制的节奏完成。, visible source ACE · Seated Chest Press. Beneath within same cohesive guide surface a simple nutrition reference strip 饮食建议 / 蛋白质与维持热量参考 with a small grain bowl line icon, no personal numbers or food diary. These two topics remain visibly present together under guide, no hide/show. Tiny 内容示意 label.
CLOSING: centered sentence 从这一组，开始。, orange rounded 开始实时分析 button plus understated 问AI教练 text link, then a clean fine divider and footer 知练同行 · 看清动作，练好下一组。. No invented copyright, user counts, testimonials or brands.
No global navbar, NO switch navigation anywhere on this homepage section. No huge illustrations, dense bullet lists, glossy gradients, excessive shadows, uniform card grid, giant metric numerals or repeated demo labels. Richness comes from balanced product previews, coherent exercise visuals and disciplined type. All example data clearly demonstration. Show complete bottom sections with nothing cut off.
```


## 完整首页交付与检查

完整图`UI图/首页完整UI.png`，1440×4646；压缩预览`UI图/首页完整UI.preview.jpg`，900×2904、240329字节。首屏采用原OPC截图前990像素在本地保留，导航按当前五项更新，并增加问AI教练文字入口；原人物、标题、按钮与演示画面不发送模型。像素对照检查确认，除导航区和AI入口外，首屏与原截图一致。

已查看完整长图：首屏、动作调整示意、训练计划与进展、知识与饮食指导、结尾操作与页脚全部连续呈现；首页没有选择切换栏或隐藏的功能介绍。计划小图的日期带仅为产品演示内容，不是首页切换控制；知识与饮食同时可见。人物前后对照、计划与知识预览分别标示意，不计真实使用与改善结果。完整图是设计合成，不是已实施页面，不代表滚动动效已完成；当前应用源文件没有改动。

下滑原图787×1998按统一1440宽度缩放合成，局部文字以开发文档为准；实际开发采用真实HTML、项目素材与组件，不把整页长图作为页面背景，也不以生成文本替代接口结果。原先单一舞台、双卡和左右分栏首屏均不采用。


## 我的训练／训练计划图稿检查

2026年11月10日已生成并查看`UI图/02_训练计划.preview.jpg`，原图1568×1003。主导航五项齐全且我的训练选中；训练计划/训练记录/训练回顾保持同一父模块。周二11月10日与示例日期一致，今日1/3个动作、33%，本周0/3次、0%口径区分；已完成、待继续、未开始三态清楚，主操作为继续训练，纠正待办仅一行。没有重复最近历史、营养或知识模块。

本张是待审阅设计稿，不代表已实现。生成图右栏额外出现“本周完成训练／今日完成动作”的重复解释行，实施时省略该行，依靠单位与对应区域标题区分；UI方案标识实际保留一处，避免重复。原品牌标识应复用项目已有SVG，不照生成图重新绘制。首页已获确认且本次未改动。临时生图环境由入口清理，应用源文件不改。


## 全页面设计覆盖清单

用户2026年11月10日要求全部页面设计。当前范围为57张页面/状态图：2张已确认稿保留，26张新增/重绘，29张基于第四轮稳定布局复用并更新共同组件。每个页面有明确归属和数据语义；不是57个独立业务路由。新图完成后仅记为待审阅，不自动回填用户确认。机器可核对清单为`页面设计清单.json`，内含每页提示词、来源、状态与尺寸；可复现流程为`生成与核对UI.py`。

| 文件 | 页面/状态 | 处理方式 | 路由/覆盖 |
| --- | --- | --- | --- |
| 首页完整UI.png | 完整首页 | 已确认保留 | / |
| 02_训练计划.png | 我的训练／训练计划 | 已确认保留 | /training/plan |
| 03_知识问答.png | 动作知识问答 | 新增/重绘 | /coach/knowledge |
| 04_饮食建议.png | 饮食建议／摄入结果 | 新增/重绘 | /coach/nutrition |
| 05_纠正任务.png | 纠正与复测任务 | 新增/重绘 | /coach/tasks |
| 06_前后对比.png | 前后对比回看 | 新增/重绘 | /coach/tasks/{id}/compare |
| 07_训练回顾.png | 本周训练回顾 | 新增/重绘 | /training/review |
| 08_今日训练_手机.png | 训练计划／手机 | 新增/重绘 | /training/plan |
| 09_计划创建.png | 创建训练计划 | 新增/重绘 | /training/plans/new |
| 10_计划编辑.png | 编辑计划草稿 | 新增/重绘 | /training/plans/{id}/edit |
| 11_计划确认.png | 计划预览与启用 | 新增/重绘 | /training/plans/{id}/activate |
| 12_训练执行.png | 逐组训练执行 | 新增/重绘 | /training/workouts/{id} |
| 13_训练结束.png | 训练完成与结果 | 新增/重绘 | /training/workouts/{id}/summary |
| 14_动作知识目录.png | 动作知识目录 | 新增/重绘 | /coach/knowledge?view=actions |
| 15_动作知识详情.png | 动作知识详情 | 新增/重绘 | /actions/{id} |
| 16_知识来源详情.png | 知识来源与引用详情 | 新增/重绘 | /coach/knowledge/{id} |
| 17_摄入参考填写.png | 饮食参考输入与资料采用 | 新增/重绘 | /coach/nutrition?view=input |
| 18_饮食问答.png | 饮食知识问答 | 新增/重绘 | /coach/nutrition?view=ask |
| 19_报告导出.png | 训练报告导出 | 新增/重绘 | /records#report |
| 20_AI训练复盘.png | AI训练复盘 | 新增/重绘 | /records#ai-report |
| 21_计划空态.png | 尚未创建计划 | 新增/重绘 | /training/plan |
| 22_训练备注.png | 负重与训练备注 | 新增/重绘 | /records/{id}#note |
| 23_功能边界状态.png | 知识营养复测的边界状态 | 新增/重绘 | 知识无匹配／营养缺项／复测不可比 |
| 24_确认与版本冲突.png | 重要确认与版本冲突 | 新增/重绘 | 替换／并发版本／临时内容失效 |
| 25_实时分析准备.png | 实时分析准备 | 既有设计复用 | /realtime |
| 26_实时分析.png | 实时分析进行中 | 既有设计复用 | /realtime |
| 27_视频选择.png | 视频选择与上传 | 既有设计复用 | /video |
| 28_视频处理中.png | 视频分析处理中 | 既有设计复用 | /video/analyses/{id} |
| 29_视频分析结果.png | 视频分析结果 | 既有设计复用 | /video/analyses/{id} |
| 30_训练记录.png | 训练记录列表 | 既有设计复用 | /records?view=list |
| 31_训练详情.png | 训练记录详情 | 既有设计复用 | /records/{id} |
| 32_训练趋势.png | 单项训练趋势 | 既有设计复用 | /records |
| 33_个人中心.png | 个人中心 | 既有设计复用 | /account |
| 34_健身档案.png | 健身档案 | 既有设计复用 | /account/training-profile |
| 35_设置.png | 设置总览 | 既有设计复用 | /settings |
| 36_AI服务设置.png | AI服务设置 | 既有设计复用 | /account/ai |
| 37_登录管理.png | 登录管理 | 既有设计复用 | /account/sessions |
| 38_修改密码.png | 修改密码 | 既有设计复用 | /account/password |
| 39_登录.png | 登录 | 既有设计复用 | /login |
| 40_注册.png | 注册与首次账号 | 既有设计复用 | /register |
| 41_账号管理.png | 管理员账号管理 | 既有设计复用 | /admin/users |
| 42_动作选择.png | 备用动作选择 | 既有设计复用 | /actions |
| 43_训练准备.png | 备用训练准备 | 既有设计复用 | /prepare |
| 44_AI教练.png | 通用AI教练 | 既有设计复用 | /coach |
| 45_首页手机首屏.png | 首页手机首屏 | 既有设计复用 | responsive |
| 46_实时分析手机.png | 实时分析手机准备 | 既有设计复用 | responsive |
| 47_视频结果手机.png | 视频分析结果手机 | 既有设计复用 | responsive |
| 48_训练记录手机.png | 训练记录手机 | 既有设计复用 | responsive |
| 49_登录手机.png | 登录手机 | 既有设计复用 | responsive |
| 50_知识问答手机.png | 动作知识问答手机 | 新增/重绘 | /coach/knowledge |
| 51_饮食建议手机.png | 饮食建议手机 | 新增/重绘 | /coach/nutrition |
| 52_训练执行手机.png | 逐组执行手机 | 新增/重绘 | /training/workouts/{id} |
| 53_设置手机.png | 设置手机 | 既有设计复用 | responsive |
| 54_相机中断.png | 相机中断手机 | 既有设计复用 | responsive |
| 55_保存失败.png | 记录保存失败 | 既有设计复用 | save_failed |
| 56_原片缺失.png | 原片缺失与重新选择 | 原界面局部合成 | 完整桌面结果状态，另附手机变体 |
| 57_访问与空数据.png | 访问限制与空数据 | 新增/重绘 | /unavailable |


复用图保持既有真实页面结构，仅更新全局五项导航、品牌及训练区子导航，未重绘主体，不声称真实应用已经切换。认证页无业务主导航，仍复用同一背景与原左右分屏布局。移动首页图为原首屏响应式基准，新增纵向段落按已确认首页及响应式规范实现，不冒称整套手机版每个状态已单独截图。

`/setup`按实际代码重定向注册，不制造多余初始化页面；首位注册差异以注册状态与对应功能规则覆盖。已从产品移除的experiments/纠错标注页面不恢复。报告、AI复盘、确认冲突为弹窗或状态设计板，不伪装成独立导航页面。通用异常板覆盖无匹配、缺资料、不可比较、权限和过期；数据失败不显示0或伪成功。


## 全页面交付核对结果（2026年11月10日）

57张主页面/状态图均已落盘并校验PNG：已确认保留2张、新生成/重绘26张、沿用第四轮并统一共同导航29张。10张压缩联系表均已目视检查，合计757898字节；12个既有交互状态作为补充链接归入索引，不为既有弹窗另造重复页面。全部页面与状态入口见`UI设计索引.html`，每项标明来源、路由和需按实现规则处理的细节。页面ID/尺寸/提示词/来源与状态统一记录在`页面设计清单.json`。

三张曾超时的图（饮食手机、执行手机、访问与空数据）只重试各自任务，已成功；没有重做已完成整批。原首页与已确认训练计划未改动。登录/注册旧品牌名在本任务副本局部修正，原认证图及源码保留；其他复用页主体不重绘。业务页生成文本仍以真实接口和需求约定为准，不能把图中所有装饰字样自动变成新增功能。

索引57项及补充状态的文件路径均可解析，JavaScript语法与筛选/搜索逻辑检查通过。图集首次交付时浏览器访问被Work设置关闭，索引未作真实浏览器点击验收；随后预指导查询中浏览器已连接成功，此处仅记录当时的验证范围；应用页面、手机真机、摄像头和真实AI业务调用均不属于本次设计阶段验证。

全部新图为待用户审阅，只有首页和训练计划已有明确采用授权。可选收藏仍为后续候选，不在本轮已承诺主页面中引入新一级页面；申请数据集、算法实验和已移出的纠错标注不恢复。新增计划与知识页已覆盖创建/编辑/执行/结果/无数据/冲突等主流程，更多真实数据组合在开发实现时按契约验收，不以有限图稿冒称穷尽所有运行情形。


## 预指导反馈驱动的局部修订要求

用户2026年11月10日提供查询截图：总分81，评语“知形健身AI视频动作分析专业；建议增加动作纠正提示功能。”。本轮新增优先项为实际纠正提示的显著与可执行展示，详见开发文档第16节。原57张设计“已生成”不等于这些修订已完成。

优先调整26_实时分析、29_视频分析结果、31_训练详情，以及46_实时分析手机、47_视频结果手机；必要时新增对应问题状态局部，而非更改已确认首页和训练计划。每页须首层出现问题＋短调整指令＋证据时间／定位；真实数据、取景与过期撤回边界保留。此前复用稿暂标待按评审意见局部加强，不再简单认定只更新导航就满足本轮需求。其他图保持待审阅，界面不得为展示效果伪造真实检出。


## 纠正提示显著性修订的确定方向

按用户最新反馈，五张待改稿优先加强现有纠正提示展示：播放器旁/手机播放器下方常驻“动作纠正提示”，首条问题与一句“下一次这样做”默认可见，时间与定位回看紧邻；AI解读是次级操作，不作为发现纠错建议的前提。详情按同一证据重用。不增加满屏红色、多重弹窗或装饰动画制造显著性。当前仅规格更新，这五张PNG尚未完成此局部修订；不得把索引中的原图声称已修改。


## 评审整改生图：视频分析结果页

用户要求先生图查看已有纠正提示更明显的效果。本次仅重绘`29_视频分析结果.png`，不是再次重画整套页面。核心结构为左侧大播放器、右侧常驻“动作纠正提示”：需要调整的问题、下一次这样做、关注部位、发生时间、定位回看和次级AI解释。默认展开第一条，其他问题收起为轻列表。完整生图提示记录在页面设计清单该条revision_prompt中，调用本机已授权原生imagegen兼容入口，不传私人参考视频。

任务完成/保存与动作质量分别显示；图内问题、人物、骨架与时间都是排版示例，不是算法检出证据。复测任务入口放在次级位置，不能遮挡本次纠正提示。此图待用户审阅，不因生图成功认定功能已实现。


本次结果页原图1586×992，已查看压缩预览：右侧纠正区首层可见，定位按钮明确，AI为次级入口。图中身体现象与建议均为示例；实施须绑定真实issue且避免机位/侧别误判，不照生成照片补造骨架与证据。其余4张预指导整改稿尚未局部重绘。原应用未修改，临时生图目录已自动清理。


## 纠正提示完整流程修订批次

用户已认可视频结果页，继续授权实时与全部有效页面设计。按相同视觉层级修订9张，首页和训练计划不改：26实时进行中先出图，46实时手机其次；其后25准备、31详情、47视频手机、54相机中断、55保存失败；补58实时反馈状态板、59详情手机。总资产数59。旧57张清单为历史交付数量，当前以页面设计清单和索引为准。

关键差异：实时不提供历史定位/录像假入口；结果页才有源时间定位。默认纠正指令与分析结果绑定，AI只是可选解释。准备/遮挡/暂停/失败均在原位置展示实际状态，不能让示例错误留在界面。新生图不等于应用已实现，图内数据为示例，真实规则与接口优先。


## 以现有前端为底图的局部合成稿

用户明确要求基于现有画面，已停止新侧栏实时页的批量生图。当前26/29等10张使用原截图和原演示素材在本地合成，不是原生生图重新设计整页；复现脚本为基于现有界面的纠正提示设计.py，源图路径见清单revision_source。实时原宽画面/原工具栏/原下方设置保留，增强原左下提示；视频保留原播放器与下方反馈，只增强原右侧卡。两张主图局部区域外像素对照通过。

截图里的旧导航文字仅作为现有布局基准；实施仍按已确认首页/实时/视频/我的训练/健身指南统一接入。先前新人物与新两栏布局不采用，当前局部稿待审阅。准备、中断与保存异常原状态复用，未凭图添加真实问题；手机沿用原截图的阅读顺序，最终固定导航与真实视口须实施时测试。

## 逐图按钮位置复核

本轮查看10张压缩联系表，已在同目录《开发详细文档.md》第18节补充59项资产的按钮所在区域、前后顺序、主次层级、手机重排及异常态位置；第17节继续定义各动作和接口。两节结合使用，不能只读接口表后由开发者临时挑按钮位置。

依据分为“图示”“未出图的位置补充”“素材不足”。首页原CTA组、实时原工具栏/画面下设置、视频原右侧反馈卡沿当前图落实；更多菜单、取消、重试、删除确认等未画出的状态在第18.3节有明确落位，但不冒称已经逐个生图或获用户确认。图45复用手机首页未完整表现新增AI入口，其落位按已确认首屏CTA约定执行。

第56项旧图原为795×448的局部视频参考，位置审查时已标为不完整。本次已沿当前29/47结果页，在原路径补成1280×937完整桌面状态，并新增390×1308手机变体`56_原片缺失_手机.png`。原播放器位置显示缺片说明和“重新选择原片”，原建议/反馈保留，所有定位入口标“待匹配原片”。查看记录、问AI和重新分析保留原层级；手机底栏不再挡住反馈卡。两稿均为本地合成设计，待用户审阅。

缺片的取消选片、正在匹配、不匹配、超限、断网、成功及失权状态已补入开发文档18.6节，分别复用V12/R12，未新增分析/重试模型接口。清单、索引与联系表同步本项完成状态，索引提供手机原图链接；整体仍为59个设计清单项，手机稿作为第56项的响应式变体，不虚增为新路由。

本次未修改应用页面，未调用生成模型，也未做浏览器像素/点击验收。其他生成稿中的旧导航、品牌及残留示例状态文案仍须按当前已确认架构和真实接口语义修正，不能机械照图复制。
