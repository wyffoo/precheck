import requests
import csv
from requests.auth import HTTPBasicAuth
from concurrent.futures import ThreadPoolExecutor, as_completed

username = 'wyifan'
password = 'Wyywjh1018'

base_url = 'https://pronto.ext.net.nokia.com/prontoapi/rest/api/latest'

def safe_text(x):
    return str(x or '').replace('\n', ' ').replace('\r', ' ').strip()

def fetch_fa(fa_id):
    fa_url = f"{base_url}/faultAnalysis/{fa_id}"
    try:
        fa_resp = requests.get(fa_url, auth=HTTPBasicAuth(username, password), timeout=10)
        if fa_resp.status_code == 200:
            fa = fa_resp.json()
            return {
                'identification': safe_text(fa.get('identification')),
                'resolution': safe_text(fa.get('resolution')),
                'subSystem': safe_text(fa.get('subSystem')),
                'rootCause': safe_text(fa.get('rootCause')),
                'internalAnalysisInfo': safe_text(fa.get('internalAnalysisInfo'))
            }
    except Exception as e:
        print(f"FA Fetch Error for {fa_id}: {e}")
    return {'identification': '', 'resolution': '', 'subSystem': '', 'rootCause': '', 'internalAnalysisInfo': ''}

def fetch_all_data():
    start_at = 0
    max_results = 50
    pr_fa_data = []

    while True:
        url = f'{base_url}/problemReport?startAt={start_at}&maxResults={max_results}'
        print(f'Fetching PRs from: {url}')
        resp = requests.get(url, auth=HTTPBasicAuth(username, password))

        if resp.status_code != 200:
            print(f'Failed fetching PRs, code {resp.status_code}')
            break

        prs = resp.json().get('values', [])

        if not prs:
            print('All PRs fetched.')
            break

        fa_ids = [pr['faultAnalysisId'] for pr in prs if pr.get('state') == 'Correction Not Needed' and pr.get('faultAnalysisId')]

        # 多线程发请求
        fa_data_map = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_fa_id = {executor.submit(fetch_fa, fa_id): fa_id for fa_id in fa_ids}
            for future in as_completed(future_to_fa_id):
                fa_id = future_to_fa_id[future]
                fa_data_map[fa_id] = future.result()

        for pr in prs:
            if pr.get('state') != 'Correction Not Needed':
                continue

            fa_data = fa_data_map.get(pr.get('faultAnalysisId'), {'identification': '', 'resolution': '', 'subSystem': '', 'rootCause': '', 'internalAnalysisInfo': ''})

            pr_fa_data.append([
                pr['id'],
                safe_text(pr.get('title')),
                safe_text(pr.get('softwareRelease')),
                safe_text(pr.get('softwareBuild')),
                safe_text(pr.get('description')),
                ', '.join(pr.get('attachmentIds', [])),
                safe_text(pr.get('groupIncharge')),
                safe_text(pr.get('collaborationCNNExplanation')),
                fa_data['identification'],
                fa_data['resolution'],
                fa_data['subSystem'],
                fa_data['rootCause'],
                fa_data['internalAnalysisInfo']
            ])

        start_at += max_results

    return pr_fa_data

def save_to_csv(data, filename):
    with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow([
            'pr_id', 'title', 'softwareRelease', 'softwareBuild', 'description',
            'attachmentIds', 'groupIncharge', 'reasonCorrectionNotNeeded',
            'identification', 'resolution', 'subSystem', 'rootCause', 'explanation'
        ])
        writer.writerows(data)

if __name__ == '__main__':
    data = fetch_all_data()
    if data:
        save_to_csv(data, 'pr_fa_all_cnn.csv')
        print('Export done -> pr_fa_all_cnn.csv')
    else:
        print('No data fetched.')
