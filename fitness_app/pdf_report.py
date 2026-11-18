"""只从已冻结训练事实排版PDF；中文字体本地嵌入，无远程资源与主动AI调用。"""
from io import BytesIO
from pathlib import Path
from datetime import datetime,timezone,timedelta
from html import escape
import hashlib,json,threading,time,unicodedata
from .paths import ROOT

POLICY='training-pdf-3'
MAX_PDF_BYTES=10*1024*1024
MAX_PAGES=80
_lock=threading.RLock();_ready=False;_cmap=set()


class ReportRenderError(RuntimeError):pass
class ReportRenderCancelled(ReportRenderError):pass


def prepare_fonts():
    global _ready,_cmap
    with _lock:
        if _ready:return
        from fontTools.ttLib import TTFont as SourceFont
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        font_configs = [
            ('GuanDongBody', 'data/fonts/noto-sans-sc/GuanDongSansSC-Regular.ttf'),
            ('GuanDongHeading', 'data/fonts/noto-sans-sc/GuanDongSansSC-SemiBold.ttf')
        ]
        for name, rel_path in font_configs:
            font_path = (ROOT / rel_path).resolve()
            if not font_path.exists():
                raise ReportRenderError('font_resources')
            font = SourceFont(font_path)
            _cmap.update(font.getBestCmap())
            font.close()
            pdfmetrics.registerFont(TTFont(name, str(font_path)))
        _ready=True


def date_text(value):
    return datetime.fromtimestamp(value/1000,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M') if value else '未记录'


def clock_text(value):
    if type(value) not in (int,float):return '未记录'
    minutes,seconds=divmod(max(0,value)/1000,60);return f'{int(minutes):02d}:{seconds:04.1f}'


def render_pdf(snapshot,report_id,created_at_ms,cancelled=lambda:False):
    if cancelled():raise ReportRenderCancelled('cancelled')
    try:prepare_fonts()
    except (OSError,KeyError,StopIteration,ValueError):raise ReportRenderError('font_resources') from None
    began=time.monotonic()
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,KeepTogether
    navy=colors.HexColor('#402517');teal=colors.HexColor('#B9431C');muted=colors.HexColor('#78675B');line=colors.HexColor('#EEDCCB');soft=colors.HexColor('#FFF0DF');amber=colors.HexColor('#8A6118')
    styles={
        'body':ParagraphStyle('body',fontName='GuanDongBody',fontSize=9.2,leading=14,textColor=navy,wordWrap='CJK',spaceAfter=4),
        'muted':ParagraphStyle('muted',fontName='GuanDongBody',fontSize=8,leading=11.2,textColor=muted,wordWrap='CJK',spaceAfter=3),
        'h1':ParagraphStyle('h1',fontName='GuanDongHeading',fontSize=24,leading=33,textColor=navy,spaceAfter=14,keepWithNext=True,wordWrap='CJK'),
        'h2':ParagraphStyle('h2',fontName='GuanDongHeading',fontSize=12,leading=18,textColor=navy,spaceBefore=7,spaceAfter=5,keepWithNext=True,wordWrap='CJK'),
        'recordh1':ParagraphStyle('recordh1',fontName='GuanDongHeading',fontSize=20,leading=28,textColor=navy,spaceAfter=8,keepWithNext=True,wordWrap='CJK'),
        'h3':ParagraphStyle('h3',fontName='GuanDongHeading',fontSize=10,leading=17,textColor=navy,spaceBefore=5,spaceAfter=3,keepWithNext=True,wordWrap='CJK'),
        'amber':ParagraphStyle('amber',fontName='GuanDongBody',fontSize=9,leading=15,textColor=amber,wordWrap='CJK',spaceAfter=8),
    }
    substituted=[0]
    def clean(value):
        text=unicodedata.normalize('NFC',str(value));result=[]
        for ch in text:
            if ch in '\n\t':result.append(ch)
            elif ord(ch) in _cmap:result.append(ch)
            else:result.append(f'[U+{ord(ch):04X}]');substituted[0]+=1
        return escape(''.join(result)).replace('\n','<br/>').replace('\t','    ')
    def p(value,style='body'):return Paragraph(clean(value),styles[style])
    story=[];body_width=A4[0]-84
    def add(value,style='body'):story.append(p(value,style))
    def table(headers,rows,widths):
        data=[[p(v,'muted') for v in headers]]+[[p(v) for v in row] for row in rows]
        t=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),soft),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,0),.6,line),('LINEBELOW',(0,1),(-1,-1),.3,line),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]));story.append(t)
    def page(canvas,doc):
        if cancelled():raise ReportRenderCancelled('cancelled')
        if doc.page>MAX_PAGES or time.monotonic()-began>40:raise ReportRenderError('report_limit')
        canvas.saveState();canvas.setFont('GuanDongHeading',13);canvas.setFillColor(navy);canvas.drawString(42,A4[1]-35,'正姿智练');canvas.setFont('GuanDongBody',8);canvas.setFillColor(muted);canvas.drawRightString(A4[0]-42,A4[1]-35,'训练复盘报告');canvas.setStrokeColor(teal);canvas.setLineWidth(1.2);canvas.line(42,A4[1]-49,A4[0]-42,A4[1]-49)
        canvas.setFont('GuanDongBody',7.2);canvas.setFillColor(muted);canvas.drawString(42,29,('工程测试样例，非真人检测' if snapshot.get('fixture_label') else '训练动作与反馈')+' | '+report_id[:8]);canvas.drawRightString(A4[0]-42,29,'第 '+str(doc.page)+' 页');canvas.restoreState()
    records=snapshot['records'];ai=snapshot['ai_reports']
    add('训练复盘报告','h1')
    if snapshot.get('fixture_label'):add(snapshot['fixture_label'],'amber')
    add('生成时间：'+date_text(created_at_ms)+'（北京时间）','muted')
    add('本文件固定保留生成时的记录版本。后续修改不会自动更新这份PDF；下载前请在应用中核对是否为旧快照。','muted')
    summary=Table([[p('选定记录','muted'),p('动作类别','muted'),p('AI复盘','muted')],[p(str(len(records))+' 条','h2'),p(str(len({r['action_id'] for r in records}))+' 类','h2'),p(str(len(ai))+' 份' if ai else '未纳入','h2')]],colWidths=[body_width/3]*3)
    summary.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),soft),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]));story.extend([Spacer(1,10),summary,Spacer(1,14)])
    add('本次报告范围','h2')
    if snapshot.get('auxiliary_source_count'):add('为解释所选复测结果，另引用 '+str(snapshot['auxiliary_source_count'])+' 条原提示来源；未将其完整训练记录纳入本报告。','muted')
    table(['动作','训练开始','来源与状态'],[(r['action_name'],date_text(r['started_at_ms']),('实时' if r['source']=='camera' else '上传')+' / '+{'completed':'已完成','ended_early':'已结束' if r.get('mode')=='free' else '目标完成前结束','interrupted':'已中断','failed':'未完成'}.get(r['status'],r['status'])) for r in records],[body_width*.32,body_width*.34,body_width*.34])
    add('阅读说明','h2')
    for text in ['按动作、时间和提示部位查看反馈，并结合原片回看。','次数、可评估周期与连续观察时段分别列出。','用户自填负重和难度仅在主动选择时附入；AI解释另页标记，不替代原检测。','不含原视频、逐帧骨架、头像、完整聊天、账号凭据或密钥；生成和下载不触发AI调用。']:
        add(text,'muted')
    for index,r in enumerate(records,1):
        story.append(PageBreak());add(f'{index:02d}  '+r['action_name'],'recordh1')
        basis={'user_selected':'用户主动指定或修正','automatically_recognized':'自动识别','not_recorded':'动作来源未记录'}.get(r['exercise_basis'],'动作来源未记录')
        add(date_text(r['started_at_ms'])+' - '+date_text(r['ended_at_ms'])+'（北京时间）','muted');add('动作身份：'+basis+'；记录版本：'+str(r['state_version'])+'。','muted')
        revision=r.get('revision')
        if revision:add('视频结果：第'+str(revision['revision_index'])+'版，'+('用户指定/修正' if revision['origin']=='manual' else '自动分析')+'，生成时为当前有效修订。','muted')
        if r['status'] in {'interrupted','failed'}:add('分析曾中断或未完成；以下只包含成功保存的部分。','amber')
        elif r['status']=='ended_early' and r.get('mode')=='target':add('本次在预设目标完成前结束，以下只包含已保存结果。','amber')
        elif r['status']=='ended_early':add('本段分析已结束，保留结束前成功保存的动作与观察。','muted')
        if r.get('source_range'):add('原视频范围：'+clock_text(r['source_range']['start_ms'])+' - '+clock_text(r['source_range']['end_ms'])+'。','muted')
        completion=r['completion']
        if not completion['counting_available']:add('完成量：请查看该动作的姿态观察记录。')
        elif completion['unit']=='seconds':add('参考保持时间：'+clock_text(completion['reference_hold_ms'])+'。')
        elif completion['unit']=='per_side_reps':add(f"参考完整动作：左侧 {completion['left_count']} 次，右侧 {completion['right_count']} 次。")
        else:add('参考完整动作：'+str(completion['reference_count'])+' 次。')
        if completion['counting_available'] and completion['unit']!='seconds':add('其中有可评估判项的周期 '+str(completion['evaluable_cycles'])+'，没有可评估判项的周期 '+str(completion['unassessable_cycles'])+'；各判项范围另列。','muted')
        if r.get('target'):
            target=r['target'];unit='秒/组' if target['unit']=='seconds' else '次/侧/组' if target['unit']=='per_side_reps' else '次/组'
            add('用户预设目标：'+str(target['sets'])+' 组，每组 '+str(target['value'])+' '+unit+'；已保存完成组数 '+str(completion['completed_sets'])+'。')
        from .ai_context import report_display
        conditions=report_display({'summary':'','limitations':r.get('limitations',[])})['limitations']
        if conditions:add('拍摄与动作说明：'+'；'.join(conditions),'muted')
        add('可评估范围','h2');rules={v['code']:v for v in r['supported_items']}
        stats=[]
        for item in r['statistics']:
            title=rules.get(item['code'],{}).get('title',item['code']);side={'left':'左侧','right':'右侧','none':'本项'}.get(item['side'],item['side']);view={'front':'正面','side':'侧面','oblique':'斜侧面'}.get(item['view'],'机位未确认')
            amount=f"{item['evaluated']} 个可评估周期，{item['issue']} 个含本项参考提示" if item['unit']=='cycles' else f"可评估 {item['evaluated']/1000:.1f} 秒，其中提示区间 {item['issue']/1000:.1f} 秒"
            unknown=str(item['unknown'])+' 个周期' if item['unit']=='cycles' else f"{item['unknown']/1000:.1f} 秒"
            stats.append((title+' / '+side+' / '+view,amount,unknown))
        if stats:table(['判项与机位','已保存可评估范围','未知'],stats,[body_width*.40,body_width*.41,body_width*.19])
        else:add('请补充已保存的有效观察后查看比例。','muted')
        for status,title in [('issue','参考提示与调整方向'),('unassessable','待评估时段')]:
            parts=[v for v in r['episodes'] if v['status']==status];add(title,'h2')
            if not parts:add('未保存该类区间。','muted');continue
            for item in parts:
                name=item['message'] or rules.get(item['code'],{}).get('title',item['code']);add(name,'h3')
                base='原片时间' if item['time_base']=='source_ms' else '记录相对时间';add(base+' '+clock_text(item['start_ms'])+' - '+clock_text(item['end_ms'])+'；部位侧别 '+({'left':'左','right':'右'}.get(item['side']) or '、'.join({'left':'左','right':'右'}.get(v,v) for v in item.get('affected_sides',[])) or '未分侧')+'。','muted')
                if item['suggestion']:add(('调整参考：' if status=='issue' else '取景/复查提示：')+item['suggestion'])
                if item['reason']:
                    from .posture_rules import REASONS
                    add('不可评估原因：'+REASONS.get(item['reason'],str(item['reason'])),'muted')
                add('保存时规则：'+item['rule_version'],'muted')
        add('重叠提示合并为时间区间，不将区间个数称为错误发生次数。','muted')
        if r.get('training_note'):
            n=r['training_note'];add('用户自填记录','h2')
            if n.get('load_value') is None:add('负重：未填写。')
            else:add('负重：'+str(n['load_value'])+' '+{'kg':'公斤','lb':'磅','plate_index':'配重档位'}.get(n.get('load_unit'),'未说明单位')+'；口径：'+{'per_hand':'单手','per_side':'单侧','total':'总重','device_setting':'器械设置','unspecified':'未说明'}.get(n.get('load_basis'),'未说明')+'。')
            add('难度：'+{'easy':'轻松','moderate':'适中','hard':'吃力'}.get(n.get('difficulty'),'未填写')+'（用户自述）。')
            if n.get('note'):add('备注：'+n['note'])
            add('以上为用户自述，不是视频测得的真实载荷或疲劳，版本 '+str(n['version'])+'。','muted')
        if r.get('retest'):
            retest=r['retest'];add('单项复测','h2');add(retest['message']);add('本次复测对照绑定的动作要点。','muted')
        movement=r.get('movement_stability') or {}
        if not movement.get('comparisons'):add('组内运动观察：'+movement.get('message','没有保存足够的同组观测。'),'muted')
        else:add('组内运动观察','h2')
        for item in movement.get('comparisons',[]):
            add(item['title']+' / '+{'left':'左侧测量','right':'右侧测量','both':'双侧'}.get(item['side'],item['side']),'h3');add(item['message'])
            if item['state']=='compared':
                unit='度' if item['unit']=='degrees' else '倍躯干投影';add(f"前半段中位值 {item['before']['median']:.3f} {unit}；后半段 {item['after']['median']:.3f} {unit}。",'muted')
        if movement.get('comparisons'):add('运动变化不证明技术正误、肌肉发力、疲劳或伤病风险。','muted')
        add('分类模型：'+str(r.get('recognition_model_version') or '未记录；不补造自动识别身份')+'\n姿态规则：'+str(r.get('posture_policy_version') or '未记录')+'\n能力快照：'+r['capability_version']+'\n姿态处理策略摘要：'+r['processing_policy_id'],'muted')
    comparison=snapshot.get('comparison')
    if comparison:
        story.append(PageBreak());add('两条记录的可比对照','h1')
        if not comparison['comparable']:add(comparison['reason'] or '不满足可比前提，没有生成改善比例。')
        else:
            for pair in comparison['comparisons']:
                labels={v['code']:v.get('title',v['code']) for r in records for v in r['supported_items']}
                add(labels.get(pair['code'],pair['code'])+' / '+{'front':'正面','side':'侧面','oblique':'斜侧面'}.get(pair['view'],pair['view'])+' / '+{'left':'左侧','right':'右侧','none':'未分侧'}.get(pair['side'],pair['side']),'h2')
                for label,name in [('before','前一条'),('after','后一条')]:
                    item=pair[label];text=f"提示周期 {item['issue']} / 可评估周期 {item['evaluated']}；未知 {item['unknown']}" if pair['unit']=='cycles' else f"提示 {item['issue']/1000:.1f} 秒 / 可评估 {item['evaluated']/1000:.1f} 秒；未知 {item['unknown']/1000:.1f} 秒"
                    add(name+'：'+text)
            add('对照采用同动作、同侧别和相近机位下的记录，逐项查看前后变化。','muted')
    for report in ai:
        story.append(PageBreak());add('AI复盘与建议','h1');add('以下为AI解释区的工程排版示例，并非真实AI输出。' if snapshot.get('fixture_label') else '以下是AI生成的解释，不是新增检测结果。','amber')
        add('报告生成时间：'+date_text(report['created_at_ms'])+'；模型：'+report['model']+'；提示策略：'+report['content']['prompt_version'],'muted')
        content=report['content'];add(content['summary'])
        for item in content['explanations']:add(item['explanation']);add('建议：'+item['adjustment']);add('证据引用：'+item['evidence_id'],'muted')
        for focus in content['next_focus']:add('下次关注：'+focus['text'])
        for text in content['limitations']:add(text,'muted')
        scope=content['read_scope'];add('读取范围：'+str(scope.get('selected_records',0))+' 条直接记录，'+str(scope.get('history_records',0))+' 条历史记录（可能重叠）；可能引用当时本人档案/备注，本报告未另附其原文。','muted')
    if substituted[0]:add('部分特殊字符在本文件中用Unicode编号显示；应用内原文不变。','muted')
    output=BytesIO();document=SimpleDocTemplate(output,pagesize=A4,leftMargin=42,rightMargin=42,topMargin=70,bottomMargin=48,title='正姿智练训练复盘报告',author='正姿智练',pageCompression=1)
    document.build(story,onFirstPage=page,onLaterPages=page)
    result=output.getvalue()
    if len(result)>MAX_PDF_BYTES:raise ReportRenderError('report_limit')
    return result
