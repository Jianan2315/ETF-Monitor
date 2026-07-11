from jinja2 import Environment,FileSystemLoader,select_autoescape

def generate_html_report(results,config,root):
 tp=root/config['report']['template_file']; op=root/config['report']['output_file']; op.parent.mkdir(parents=True,exist_ok=True)
 env=Environment(loader=FileSystemLoader(str(tp.parent)),autoescape=select_autoescape(['html']))
 op.write_text(env.get_template(tp.name).render(monitor_name=config.get('monitor_name','ETF Monitor'),results=results),encoding='utf-8')
