from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class Phase1PreservationTests(TransactionTestCase):
    def test_phase1_records_survive_additive_phase2_migration(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("operations", None)])
        old_apps = executor.loader.project_state(
            [("central", "0002_alter_human_options_alter_human_username_and_more")]
        ).apps
        human = old_apps.get_model("central", "Human").objects.create(
            username="legacy@example.test",
            email="legacy@example.test",
            password="!",
            first_name="Perfil previo sintético",
        )
        business = old_apps.get_model("central", "Business").objects.create(
            name="Negocio previo sintético"
        )
        old_apps.get_model("central", "Membership").objects.create(
            business=business, human=human, role="admin", admin_seat=1
        )
        old_apps.get_model("central", "Appearance").objects.create(
            business=business, primary="#312e81"
        )
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([("operations", "0001_initial")])
            apps = executor.loader.project_state().apps
            self.assertEqual(
                apps.get_model("central", "Human").objects.get(pk=human.pk).first_name,
                "Perfil previo sintético",
            )
            self.assertEqual(
                apps.get_model("central", "Membership").objects.get(human_id=human.pk).business_id,
                business.pk,
            )
            self.assertEqual(
                apps.get_model("central", "Appearance")
                .objects.get(business_id=business.pk)
                .primary,
                "#312e81",
            )
            self.assertEqual(apps.get_model("operations", "Order").objects.count(), 0)
        finally:
            MigrationExecutor(connection).migrate([("operations", "0001_initial")])
