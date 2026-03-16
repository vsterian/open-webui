<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getVideoIndexerConfig,
		updateVideoIndexerConfig,
		verifyVideoIndexerConnection
	} from '$lib/apis/video-indexer';

	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let saveHandler: () => void;

	let loading = true;
	let verifying = false;

	// Config fields
	let ENABLED = false;
	let PROVIDER = 'azure_video_indexer';
	let ACCOUNT_NAME = '';
	let ACCOUNT_ID = '';
	let RESOURCE_GROUP = '';
	let SUBSCRIPTION_ID = '';
	let LOCATION = '';
	let TENANT_ID = '';
	let CLIENT_ID = '';
	let CLIENT_SECRET = '';
	let INDEXING_PRESET = 'Default';
	let LANGUAGE = 'en-US';
	let SONIOX_API_KEY = '';
	let SONIOX_BASE_URL = 'https://api.soniox.com/v1';
	let SONIOX_MODEL = 'stt-async-v4';
	let SONIOX_ENABLE_LANGUAGE_IDENTIFICATION = true;
	let SONIOX_LANGUAGE_HINTS = '';

	const PROVIDER_OPTIONS = [
		{ value: 'azure_video_indexer', label: 'Azure Video Indexer' },
		{ value: 'soniox', label: 'Soniox' }
	];

	const PRESET_OPTIONS = [
		'Default',
		'AudioOnly',
		'VideoOnly',
		'AdvancedAudio',
		'AdvancedVideo',
		'AdvancedAudioVideo'
	];

	const LANGUAGE_OPTIONS = [
		'en-US',
		'en-GB',
		'es-ES',
		'fr-FR',
		'de-DE',
		'it-IT',
		'pt-BR',
		'pt-PT',
		'zh-Hans',
		'zh-Hant',
		'ja-JP',
		'ko-KR',
		'ar-SA',
		'hi-IN',
		'nl-NL',
		'pl-PL',
		'ru-RU',
		'sv-SE',
		'tr-TR',
		'uk-UA',
		'ro-RO',
		'multi'
	];

	const AZURE_REGIONS = [
		'eastus',
		'eastus2',
		'westus',
		'westus2',
		'westus3',
		'centralus',
		'northcentralus',
		'southcentralus',
		'westcentralus',
		'northeurope',
		'westeurope',
		'uksouth',
		'ukwest',
		'francecentral',
		'germanywestcentral',
		'switzerlandnorth',
		'norwayeast',
		'swedencentral',
		'australiaeast',
		'southeastasia',
		'eastasia',
		'japaneast',
		'japanwest',
		'koreacentral',
		'centralindia',
		'brazilsouth'
	];

	const loadConfig = async () => {
		loading = true;
		try {
			const config = await getVideoIndexerConfig(localStorage.token);
			if (config) {
				ENABLED = config.ENABLED ?? false;
				PROVIDER = config.PROVIDER ?? 'azure_video_indexer';
				ACCOUNT_NAME = config.ACCOUNT_NAME ?? '';
				ACCOUNT_ID = config.ACCOUNT_ID ?? '';
				RESOURCE_GROUP = config.RESOURCE_GROUP ?? '';
				SUBSCRIPTION_ID = config.SUBSCRIPTION_ID ?? '';
				LOCATION = config.LOCATION ?? '';
				TENANT_ID = config.TENANT_ID ?? '';
				CLIENT_ID = config.CLIENT_ID ?? '';
				CLIENT_SECRET = config.CLIENT_SECRET ?? '';
				INDEXING_PRESET = config.INDEXING_PRESET ?? 'Default';
				LANGUAGE = config.LANGUAGE ?? 'en-US';
				SONIOX_API_KEY = config.SONIOX_API_KEY ?? '';
				SONIOX_BASE_URL = config.SONIOX_BASE_URL ?? 'https://api.soniox.com/v1';
				SONIOX_MODEL = config.SONIOX_MODEL ?? 'stt-async-v4';
				SONIOX_ENABLE_LANGUAGE_IDENTIFICATION =
					config.SONIOX_ENABLE_LANGUAGE_IDENTIFICATION ?? true;
				SONIOX_LANGUAGE_HINTS = (config.SONIOX_LANGUAGE_HINTS ?? []).join(', ');
			}
		} catch (e) {
			toast.error(`Failed to load Video Indexer config: ${e}`);
		}
		loading = false;
	};

	const handleSave = async () => {
		try {
			await updateVideoIndexerConfig(localStorage.token, {
				ENABLED,
				PROVIDER,
				ACCOUNT_NAME,
				ACCOUNT_ID,
				RESOURCE_GROUP,
				SUBSCRIPTION_ID,
				LOCATION,
				TENANT_ID,
				CLIENT_ID,
				CLIENT_SECRET,
				INDEXING_PRESET,
				LANGUAGE,
				SONIOX_API_KEY,
				SONIOX_BASE_URL,
				SONIOX_MODEL,
				SONIOX_ENABLE_LANGUAGE_IDENTIFICATION,
				SONIOX_LANGUAGE_HINTS: SONIOX_LANGUAGE_HINTS
					.split(',')
					.map((item) => item.trim())
					.filter(Boolean)
			});
			saveHandler();
		} catch (e) {
			toast.error(`Failed to save: ${e}`);
		}
	};

	const handleVerify = async () => {
		// Save first, then verify
		await handleSave();
		verifying = true;
		try {
			const result = await verifyVideoIndexerConnection(localStorage.token);
			if (result?.status === 'ok') {
				toast.success($i18n.t('Connection verified successfully!'));
			} else {
				toast.error($i18n.t('Verification failed'));
			}
		} catch (e) {
			toast.error(`${$i18n.t('Connection failed')}: ${e}`);
		}
		verifying = false;
	};

	onMount(() => {
		loadConfig();
	});
</script>

{#if loading}
	<div class="flex justify-center py-8">
		<Spinner />
	</div>
{:else}
	<form
		class="flex flex-col h-full justify-between text-sm"
		on:submit|preventDefault={handleSave}
	>
		<div class="overflow-y-scroll scrollbar-hidden h-full pr-1.5">
			<div class="space-y-3">
				<!-- Header -->
				<div>
					<div class="mb-2">
						<div class="flex justify-between items-center">
							<div class="font-medium">
								{$i18n.t('Azure AI Video Indexer')}
							</div>
						</div>
						<div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
							{$i18n.t(
								'Upload video or audio in chat and route processing to Azure Video Indexer or Soniox based on the selected provider.'
							)}
						</div>
					</div>
				</div>

				<!-- Enabled toggle -->
				<div class="flex items-center justify-between">
					<div class="font-medium">{$i18n.t('Enable Video Indexer')}</div>
					<div>
						<button
							type="button"
							class="relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out {ENABLED
								? 'bg-blue-600'
								: 'bg-gray-200 dark:bg-gray-700'}"
							role="switch"
							aria-checked={ENABLED}
							on:click={() => (ENABLED = !ENABLED)}
						>
							<span
								class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out {ENABLED
									? 'translate-x-5'
									: 'translate-x-0'}"
							/>
						</button>
					</div>
				</div>

				{#if ENABLED}
					<hr class="border-gray-100 dark:border-gray-850" />

					<div>
						<div class="text-xs font-medium mb-1">{$i18n.t('Analyzer Provider')}</div>
						<select
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							bind:value={PROVIDER}
						>
							{#each PROVIDER_OPTIONS as option}
								<option value={option.value}>{option.label}</option>
							{/each}
						</select>
					</div>

					<!-- Azure Subscription -->
					<div class="space-y-3">
						<div class="text-sm font-medium text-gray-700 dark:text-gray-300">
							{$i18n.t('Azure Subscription')}
						</div>

						<div>
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Subscription ID')}
							</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
								bind:value={SUBSCRIPTION_ID}
								required
							/>
						</div>

						<div>
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Resource Group')}
							</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								placeholder="rg-video-indexer"
								bind:value={RESOURCE_GROUP}
								required
							/>
						</div>
					</div>

					<hr class="border-gray-100 dark:border-gray-850" />

					<!-- Video Indexer Account -->
					<div class="space-y-3">
						<div class="text-sm font-medium text-gray-700 dark:text-gray-300">
							{$i18n.t('Video Indexer Account')}
						</div>

						<div>
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Account Name')}
							</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								placeholder="my-vi-account"
								bind:value={ACCOUNT_NAME}
								required
							/>
						</div>

						<div>
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Account ID')}
							</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
								bind:value={ACCOUNT_ID}
								required
							/>
							<div class="text-xs text-gray-400 mt-0.5">
								{$i18n.t('Found in the Azure Portal under your Video Indexer resource → Properties → Account ID')}
							</div>
						</div>

						<div>
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Location (Region)')}
							</div>
							<select
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								bind:value={LOCATION}
								required
							>
								<option value="" disabled>{$i18n.t('Select a region')}</option>
								{#each AZURE_REGIONS as region}
									<option value={region}>{region}</option>
								{/each}
							</select>
						</div>
					</div>

					<hr class="border-gray-100 dark:border-gray-850" />

					<!-- Service Principal Authentication -->
					<div class="space-y-3">
						<div class="text-sm font-medium text-gray-700 dark:text-gray-300">
							{$i18n.t('Service Principal (Entra ID App Registration)')}
						</div>

						<div>
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Tenant ID')}
							</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
								bind:value={TENANT_ID}
								required
							/>
						</div>

						<div>
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Client ID (Application ID)')}
							</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
								bind:value={CLIENT_ID}
								required
							/>
						</div>

						<div>
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Client Secret')}
							</div>
							<SensitiveInput
								placeholder="Enter client secret..."
								bind:value={CLIENT_SECRET}
								required
							/>
						</div>
					</div>

					<hr class="border-gray-100 dark:border-gray-850" />

					<!-- Indexing Settings -->
					<div class="space-y-3">
						<div class="text-sm font-medium text-gray-700 dark:text-gray-300">
							{$i18n.t('Indexing Settings')}
						</div>

						<div>
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Indexing Preset')}
							</div>
							<select
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								bind:value={INDEXING_PRESET}
							>
								{#each PRESET_OPTIONS as preset}
									<option value={preset}>{preset}</option>
								{/each}
							</select>
							<div class="text-xs text-gray-400 mt-0.5">
								{$i18n.t(
									'"Default" = Audio+Video Standard. Use "AudioOnly" for faster/cheaper transcript-only processing.'
								)}
							</div>
						</div>

						<div>
							<div class="text-xs font-medium mb-1">
								{$i18n.t('Default Language')}
							</div>
							<select
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								bind:value={LANGUAGE}
							>
								{#each LANGUAGE_OPTIONS as lang}
									<option value={lang}>{lang}</option>
								{/each}
							</select>
							<div class="text-xs text-gray-400 mt-0.5">
								{$i18n.t('Use "multi" for automatic multi-language detection.')}
							</div>
						</div>
					</div>

					<hr class="border-gray-100 dark:border-gray-850" />

					<div class="space-y-3">
						<div class="text-sm font-medium text-gray-700 dark:text-gray-300">
							{$i18n.t('Soniox Settings')}
						</div>

						<div>
							<div class="text-xs font-medium mb-1">{$i18n.t('Soniox API Key')}</div>
							<SensitiveInput placeholder="Enter Soniox API key..." bind:value={SONIOX_API_KEY} />
						</div>

						<div>
							<div class="text-xs font-medium mb-1">{$i18n.t('Soniox Base URL')}</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								bind:value={SONIOX_BASE_URL}
							/>
						</div>

						<div>
							<div class="text-xs font-medium mb-1">{$i18n.t('Soniox Model')}</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								bind:value={SONIOX_MODEL}
							/>
						</div>

						<div class="flex items-center justify-between">
							<div class="text-xs font-medium">{$i18n.t('Enable Soniox Language Identification')}</div>
							<button
								type="button"
								class="relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out {SONIOX_ENABLE_LANGUAGE_IDENTIFICATION
									? 'bg-blue-600'
									: 'bg-gray-200 dark:bg-gray-700'}"
								role="switch"
								aria-checked={SONIOX_ENABLE_LANGUAGE_IDENTIFICATION}
								on:click={() =>
									(SONIOX_ENABLE_LANGUAGE_IDENTIFICATION = !SONIOX_ENABLE_LANGUAGE_IDENTIFICATION)}
							>
								<span
									class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out {SONIOX_ENABLE_LANGUAGE_IDENTIFICATION
										? 'translate-x-5'
										: 'translate-x-0'}"
								/>
							</button>
						</div>

						<div>
							<div class="text-xs font-medium mb-1">{$i18n.t('Soniox Language Hints')}</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								placeholder="ro, en"
								bind:value={SONIOX_LANGUAGE_HINTS}
							/>
							<div class="text-xs text-gray-400 mt-0.5">
								{$i18n.t('Comma-separated ISO language codes, e.g. ro, en.')}
							</div>
						</div>
					</div>

					<hr class="border-gray-100 dark:border-gray-850" />

					<!-- Test Connection -->
					<div class="flex justify-end">
						<button
							type="button"
							class="text-sm px-4 py-2 rounded-lg {verifying
								? 'bg-gray-200 dark:bg-gray-700 cursor-wait'
								: 'bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700'} transition"
							on:click={handleVerify}
							disabled={verifying}
						>
							{#if verifying}
								<div class="flex items-center gap-2">
									<Spinner className="size-3" />
									{$i18n.t('Verifying...')}
								</div>
							{:else}
								{$i18n.t('Test Connection')}
							{/if}
						</button>
					</div>
				{/if}
			</div>
		</div>

		<!-- Save button -->
		<div class="flex justify-end pt-3">
			<button
				class="px-4 py-2 text-sm font-medium rounded-lg bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition"
				type="submit"
			>
				{$i18n.t('Save')}
			</button>
		</div>
	</form>
{/if}
