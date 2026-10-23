"""Small local desktop launcher. Uses the selected assistant, never an embedded API key."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]


def find_tools():
    """Detect installed tools without changing system PATH or downloading binaries."""
    found={name:shutil.which(name) for name in ('ffmpeg','ffprobe')}
    if os.name=='nt' and not all(found.values()):
        local=Path(os.environ.get('LOCALAPPDATA',''))/'Microsoft/WinGet/Packages'
        for name in found:
            if found[name]:continue
            choices=sorted(local.glob('*/**/bin/'+name+'.exe')) if local.is_dir() else []
            if choices:found[name]=str(choices[-1])
    return found


class Launcher:
    def __init__(self):
        self.window=tk.Tk();self.window.title('Apex Clipper');self.window.geometry('840x650')
        self.events=queue.Queue();self.busy=False;self.current=None;self.workdir=None
        self.source=tk.StringVar();self.status=tk.StringVar(value='选择录像后开始；AI 审核仍由你当前使用的助手完成。')
        self.ignore_marks=tk.BooleanVar(value=False)
        self.mode=tk.StringVar(value='strict')
        box=ttk.Frame(self.window,padding=16);box.pack(fill='both',expand=True)
        ttk.Label(box,text='Apex 录像剪辑',font=('',18)).pack(anchor='w')
        ttk.Label(box,text='选择录像 → 环境检查 → AI按任务卡审核 → 自动导出').pack(anchor='w',pady=(4,12))
        row=ttk.Frame(box);row.pack(fill='x')
        ttk.Entry(row,textvariable=self.source).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='选择录像',command=self.choose).pack(side='left',padx=(8,0))
        ttk.Radiobutton(box,text='精选击倒：保留原有150伤害和完整审核规则',variable=self.mode,value='strict').pack(anchor='w',pady=(8,0))
        ttk.Radiobutton(box,text='直接导出标记片段：不做击倒或伤害审核',variable=self.mode,value='marked').pack(anchor='w')
        ttk.Checkbutton(box,text='本次明确忽略标记，使用通常的全片分钟筛选',variable=self.ignore_marks).pack(anchor='w',pady=8)
        buttons=ttk.Frame(box);buttons.pack(fill='x',pady=8)
        self.controls=[]
        for label,command in [('检查环境',self.doctor),('安装项目依赖',self.setup),('开始剪辑',self.start),('继续已有任务',self.resume)]:
            b=ttk.Button(buttons,text=label,command=command);b.pack(side='left',padx=(0,8));self.controls.append(b)
        ttk.Label(box,textvariable=self.status,wraplength=790).pack(fill='x',pady=8)
        self.info=tk.Text(box,height=19,wrap='word');self.info.pack(fill='both',expand=True)
        actions=ttk.Frame(box);actions.pack(fill='x',pady=(10,0))
        for label,command in [('复制给AI的任务说明',self.copy_task),('导入AI答案',self.load_answer),('刷新当前任务',self.advance),('打开结果目录',self.open_folder)]:
            b=ttk.Button(actions,text=label,command=command);b.pack(side='left',padx=(0,8));self.controls.append(b)
        self.window.after(100,self.poll)

    def choose(self):
        value=filedialog.askopenfilename(title='选择原始录像',filetypes=[('视频','*.mp4 *.mkv *.mov *.webm *.avi'),('所有文件','*.*')])
        if value:self.source.set(value)

    def append(self,value):
        self.info.delete('1.0','end');self.info.insert('1.0',value)

    def worker(self,label,fn):
        if self.busy:return
        self.busy=True;self.status.set(label)
        for b in self.controls:b.configure(state='disabled')
        def work():
            try:self.events.put(('ok',fn()))
            except Exception as exc:self.events.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        try:
            kind,value=self.events.get_nowait();self.busy=False
            for b in self.controls:b.configure(state='normal')
            if kind=='error':self.status.set('已停止，进度保留。');self.append(str(value))
            elif isinstance(value,dict) and 'stage' in value:self.present(value)
            else:self.status.set('检查完成');self.append(json.dumps(value,ensure_ascii=False,indent=2) if isinstance(value,dict) else str(value))
        except queue.Empty:pass
        self.window.after(100,self.poll)

    def config(self):
        from config import load_config
        config=load_config();found=find_tools()
        for name,value in found.items():
            if config['tools'][name] is None and value:config['tools'][name]=value
        return config

    def doctor(self):
        def check():
            from environment import doctor
            from config import apply_tools
            cfg=self.config();apply_tools(cfg)
            return doctor(cfg,self.source.get() or None)
        self.worker('正在检查 Python、依赖和视频工具……',check)

    def setup(self):
        if not messagebox.askyesno('安装依赖','将联网下载本项目依赖到独立 .venv；不修改系统 Python。继续安装？'):return
        def install():
            from environment import setup
            setup();return '依赖已安装。请关闭此窗口，再重新打开启动器。'
        self.worker('正在下载和安装依赖……',install)

    def start(self):
        if not self.source.get():messagebox.showinfo('选择录像','请先选择一个原始录像文件。');return
        source=self.source.get();ignore=self.ignore_marks.get();mode=self.mode.get()
        sidecar=Path(source+'.apex-markers.json')
        if mode=='strict' and not ignore and sidecar.is_file():
            try:
                data=json.loads(sidecar.read_text(encoding='utf-8-sig'))
                if data.get('status')=='complete' and data.get('markers')==[]:
                    if not messagebox.askyesno('本次没有标记','这段录像没有精彩标记。是否明确改用通常的全片分钟筛选？选择否会退出本次操作。'):return
                    ignore=True;self.ignore_marks.set(True)
            except (ValueError,AttributeError):pass
        def begin():
            from common import save_json
            from config import apply_tools
            from environment import doctor
            from workflow import start
            cfg=self.config();apply_tools(cfg)
            if mode=='marked':
                from marked_export import export_marked
                return export_marked(source)
            report=doctor(cfg,source)
            if not report['ready_for_local_scan']:
                return {'stage':'needs_attention','reason':'请先处理环境检查中的缺项。','environment':report}
            local=ROOT/'.local';local.mkdir(exist_ok=True)
            config_path=local/'launcher-config.json';save_json(config_path,cfg)
            return start(source,config_path=config_path,ignore_markers=ignore)
        self.worker('正在准备录像和第一项审核任务……',begin)

    def resume(self):
        root=filedialog.askdirectory(title='选择包含 job.json 的任务目录')
        if root:self.workdir=root;self.advance()

    def advance(self):
        if not self.workdir:return
        root=self.workdir
        def advance():
            from workflow import next_task
            return next_task(root)
        self.worker('正在恢复任务……',advance)

    def present(self,value):
        self.current=value;self.workdir=value.get('workdir') or self.workdir
        labels={'review_required':'等待 AI 看图并回答','needs_attention':'需要处理，未绕过审核',
                'complete':'剪辑完成，媒体校验通过','complete_no_events':'审核完成，没有符合规则的事件',
                'marked_export_complete':'标记片段已导出，未做击倒与伤害审核'}
        self.status.set(labels.get(value['stage'],value['stage']))
        if value['stage']=='review_required':
            names={'calibration':'确认识别区域','matches':'确认对局范围','hud':'读取战绩数字','page':'寻找可能的精彩事件',
                   'event':'确认精彩事件','ammo':'检查开火起点','interval':'检查完整片段','damage':'核对片段伤害'}
            self.append('当前需要AI完成：'+names.get(value['kind'],'画面审核')+'\n\n点击“复制给AI的任务说明”，交给能读取本地图片的助手。\n在助手中选用Luna即可尝试；本工具不会切换模型。\n\n如果助手已加载本skill，可直接让它自动继续，不必逐次复制。\n\n工作进度已保存在：\n'+str(self.workdir))
        elif value['stage'] in ('complete','marked_export_complete'):
            files=value.get('outputs',[])
            self.append('输出目录：\n'+str(self.workdir)+'\n\n已生成 '+str(len(files))+' 个视频文件。'+('\n\n这些是直接切出的标记片段，未审核击倒或实际伤害。' if value['stage']=='marked_export_complete' else '\n\n已按精选击倒规则完成审核与媒体校验；未标记或未入选部分不代表全片已检查。'))
        elif value['stage']=='complete_no_events':
            self.append('审核范围内没有满足当前规则的事件，因此没有生成精彩集锦。\n\n任务目录：'+str(self.workdir))
        else:
            details=value.get('reason','当前需要补充审核或处理环境问题。')
            if any(e.get('code')=='hud_unresolved_review_required' for e in value.get('errors',[])):
                details='仍有较多看不清的战绩范围，需要补核，或由你明确决定是否只交付已确认部分。程序没有代替你跳过。'
            self.append(details+'\n\n进度目录：'+str(self.workdir or '尚未创建任务'))

    def copy_task(self):
        if not self.current or self.current.get('stage')!='review_required':return
        task_path=self.current['task_path'];root=self.workdir
        text=('请实际读取任务卡和其中的本地图片，按result_schema回答。不要猜数字，不要修改脚本或审核记录。'
              +'\n任务卡：'+task_path+'\n任务目录：'+root
              +'\n答案保存为JSON，只有task_id、outcome、result。看不清用need_detail或uncertain，result=null。'
              +'\n若已加载apex-clipper，提交后继续next/submit，直到完成或明确需要用户处理。')
        self.window.clipboard_clear();self.window.clipboard_append(text);self.status.set('任务说明已复制。')

    def load_answer(self):
        if not self.workdir:return
        answer=filedialog.askopenfilename(title='选择 AI 保存的答案 JSON',filetypes=[('JSON','*.json')])
        if not answer:return
        root=self.workdir
        def apply():
            from workflow import submit
            return submit(root,answer,'User-selected assistant / visual review')
        self.worker('正在校验答案并继续……',apply)

    def open_folder(self):
        if not self.workdir:return
        if os.name=='nt':os.startfile(self.workdir)
        elif sys.platform=='darwin':subprocess.Popen(['open',self.workdir])
        else:subprocess.Popen(['xdg-open',self.workdir])

    def main(self):self.window.mainloop()


if __name__=='__main__':
    from environment import env_python
    interpreter=env_python()
    if interpreter.is_file() and Path(sys.prefix).resolve()!=interpreter.parent.parent.resolve() and os.environ.get('APEX_NO_VENV')!='1':
        raise SystemExit(subprocess.call([str(interpreter),str(Path(__file__).resolve())]))
    Launcher().main()
