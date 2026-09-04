"""Interactive visual check using disposable inventory and preferences."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from liquid_nitrogen_tank_manager import FreezerManagerApp
from liquid_nitrogen_tank_store import BoxSample, FreezerRepository

with TemporaryDirectory() as folder:
    repository = FreezerRepository(Path(folder) / 'preview.db', Path(folder) / 'missing.json')
    repository.set_box_sample('11', 'A1', BoxSample(sample_name='RAW264.7', sample_type='Mouse macrophage',
                                                 stored_by='Horace', stored_date='2026-08-19', notes='Frozen at passage 3'))
    app = FreezerManagerApp(repository)
    app.change_language('en')
    app.geometry('1240x780')
    app.show_inventory_page()
    app.title('Bilingual UI verification')
    app.after(600000, app.destroy)
    app.mainloop()
