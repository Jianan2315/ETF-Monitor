from pathlib import Path

from src.config_loader import load_config
from src.data_fetcher import fetch_asset_data
from src.indicators import calculate_indicators
from src.strategy import evaluate_strategy
from src.storage import save_run_result
from src.html_report import generate_html_report
from src.llm_client import generate_summary
from src.email_sender import send_notification_email, should_send_email
from src.logger import configure_logging
from src.position import load_transactions, calculate_position

ROOT=Path(__file__).resolve().parent


def build_chart_data(history):
    return [{'date': row['date'].strftime('%Y-%m-%d') if hasattr(row['date'],'strftime') else str(row['date']), 'price':round(float(row['close']),4)} for _,row in history.iterrows()]


def main():
    logger=configure_logging(ROOT/'logs'/'monitor.log')
    config=load_config(ROOT/'config')
    results=[]
    transaction_path=ROOT/config['storage']['transactions_file']

    for configured_asset in config['assets']:
        if not configured_asset.get('enabled',True):
            continue
        try:
            bundle=fetch_asset_data(configured_asset,config)
            transactions=load_transactions(transaction_path,configured_asset['symbol'])
            position_metrics=calculate_position(transactions,bundle.latest.price)

            asset={**configured_asset,'position':{**configured_asset.get('position',{}),**position_metrics}}
            indicators=calculate_indicators(asset,bundle.history,bundle.latest)
            decision=evaluate_strategy(asset,indicators)
            result={
              'asset':asset,
              'latest':bundle.latest.to_dict(),
              'indicators':indicators.to_dict(),
              'position_metrics':position_metrics,
              'decision':decision.to_dict(),
              'data_source':bundle.data_source,
              'generated_at':bundle.generated_at.isoformat(),
              'chart_data':build_chart_data(bundle.history),
              'operation_history':list(reversed(transactions[-10:])),
              'llm_summary':None,
            }
            save_run_result(result,config,ROOT)

            email_needed=should_send_email(result,config['email'])
            if email_needed and config['email'].get('include_llm_summary',True) and config['llm'].get('enabled',False):
                try:
                    result['llm_summary']=generate_summary(result,config['llm'])
                except Exception:
                    logger.exception('生成模型摘要失败，继续发送无摘要邮件')

            if email_needed:
                try:
                    send_notification_email(result,config)
                except Exception:
                    logger.exception('发送邮件失败，但不影响数据和网页生成')

            results.append(result)
        except Exception:
            logger.exception('处理 %s 失败',configured_asset.get('symbol'))

    if results:
        generate_html_report(results,config,ROOT)
    logger.info('ETF Monitor finished')


if __name__=='__main__':
    main()
