import yaml
from CanvasFileFetcher import CanvasFileFetcher

config = yaml.safe_load(open('config.yml', 'r'))
fetcher = CanvasFileFetcher(config['WEBSITE'], config['ACCESS_TOKEN'], config['SAVE_DIR'])

fetcher.collect_files()

fetcher.export_to_csv('canvas_files.csv')

fetcher.download_files_from_csv('canvas_files.csv')
