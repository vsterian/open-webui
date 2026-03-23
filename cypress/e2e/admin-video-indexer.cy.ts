// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../support/index.d.ts" />
import { adminUser } from '../support/e2e';

// Tests for Admin Settings > Video Indexer panel
describe('Admin Settings - Video Indexer', () => {
	// Wait for 2 seconds after all tests to fix an issue with Cypress's video recording
	after(() => {
		// eslint-disable-next-line cypress/no-unnecessary-waiting
		cy.wait(2000);
	});

	beforeEach(() => {
		cy.loginAdmin();
		// Navigate directly to the admin Video Indexer settings page
		cy.visit('/admin/settings/video-indexer');
	});

	context('Video Indexer Tab Navigation', () => {
		it('admin can navigate to the Video Indexer settings tab', () => {
			// Verify the settings panel loads with expected form fields
			cy.contains('Video Indexer').should('be.visible');
		});

		it('admin settings sidebar shows Video Indexer tab', () => {
			// The tab should be present in the sidebar navigation
			cy.get('button[id="video-indexer"]').should('exist');
		});
	});

	context('Video Indexer Settings Form', () => {
		it('shows enable/disable toggle', () => {
			cy.contains('Enable Video Indexer').should('be.visible');
		});

		it('shows analyzer provider selector', () => {
			cy.contains('Analyzer Provider').should('be.visible');
		});

		it('shows Azure Subscription fields', () => {
			cy.contains('Subscription ID').should('be.visible');
			cy.contains('Resource Group').should('be.visible');
		});

		it('shows Video Indexer Account fields', () => {
			cy.contains('Account Name').should('be.visible');
			cy.contains('Account ID').should('be.visible');
			cy.contains('Location').should('be.visible');
		});

		it('shows Service Principal fields', () => {
			cy.contains('Tenant ID').should('be.visible');
			cy.contains('Client ID').should('be.visible');
			cy.contains('Client Secret').should('be.visible');
		});

		it('shows Indexing Settings', () => {
			cy.contains('Indexing Preset').should('be.visible');
			cy.contains('Language').should('be.visible');
		});

		it('shows Azure direct upload target fields', () => {
			cy.contains('Azure Blob Endpoint (Optional)').should('be.visible');
			cy.contains('Azure Blob Container (Optional)').should('be.visible');
		});

		it('shows Soniox settings fields', () => {
			cy.contains('Soniox Settings').should('be.visible');
			cy.contains('Soniox API Key').should('be.visible');
			cy.contains('Soniox Base URL').should('be.visible');
			cy.contains('Soniox Model').should('be.visible');
		});

		it('admin can fill and save Video Indexer settings', () => {
			// Type into the subscription ID field
			cy.get('input[placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"]')
				.first()
				.clear()
				.type('test-subscription-id');

			cy.get('input[placeholder="https://<account>.blob.core.windows.net"]')
				.clear()
				.type('https://example.blob.core.windows.net');

			cy.get('input[placeholder="video-uploads"]')
				.clear()
				.type('video-uploads');

			// Hit save
			cy.get('button').contains('Save').click();

			// Verify toast or successful save
			cy.contains('Settings saved successfully!').should('be.visible');
		});
	});
});
