# Переход от "дней мероприятия" к "сменам": EventDay -> EventShift + recruit_status
# переезжает с Event на смену. Переписано вручную вместо автогенерации (Django не
# распознал переименование как RenameModel, т.к. одновременно менялись поля, и
# сгенерировал Create+Delete, который падал на SQLite при пересборке таблицы).
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0002_alter_event_event_status'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='EventDay',
            new_name='EventShift',
        ),
        migrations.AlterModelOptions(
            name='eventshift',
            options={'verbose_name': 'Смена мероприятия', 'verbose_name_plural': 'Смены мероприятий'},
        ),
        migrations.AlterModelTable(
            name='eventshift',
            table='event_shifts',
        ),
        migrations.AlterField(
            model_name='eventshift',
            name='event',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shifts', to='blog.event'),
        ),
        migrations.AlterField(
            model_name='eventshift',
            name='location',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='event_shifts', to='blog.location'),
        ),
        migrations.AddField(
            model_name='eventshift',
            name='recruit_status',
            field=models.CharField(choices=[('open', 'Открыт'), ('closed', 'Закрыт')], default='open', max_length=20),
        ),
        migrations.RemoveField(
            model_name='event',
            name='recruit_status',
        ),
        migrations.RemoveIndex(
            model_name='eventshift',
            name='event_days_event_i_8e65d3_idx',
        ),
        migrations.AddIndex(
            model_name='eventshift',
            index=models.Index(fields=['event'], name='event_shift_event_i_147157_idx'),
        ),
    ]
