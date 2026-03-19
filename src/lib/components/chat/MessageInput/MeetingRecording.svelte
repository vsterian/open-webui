<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { tick, getContext, onMount, onDestroy } from 'svelte';

	import XMark from '$lib/components/icons/XMark.svelte';

	import dayjs from 'dayjs';
	import LocalizedFormat from 'dayjs/plugin/localizedFormat';
	dayjs.extend(LocalizedFormat);

	const i18n = getContext('i18n');

	export let recording = false;

	export let echoCancellation = true;
	export let noiseSuppression = true;
	export let autoGainControl = true;

	export let className = ' p-2.5 w-full max-w-full';

	export let onCancel = () => {};
	export let onComplete: (file: File) => void = () => {};

	type RecordingState = 'idle' | 'recording' | 'paused';

	let state: RecordingState = 'idle';
	let loading = false;
	let durationSeconds = 0;
	let durationCounter: ReturnType<typeof setInterval> | null = null;

	let stream: MediaStream | null = null;
	let mediaRecorder: MediaRecorder | null = null;
	let audioChunks: Blob[] = [];
	let observedMimeType = '';

	const MIN_DECIBELS = -45;
	let VISUALIZER_BUFFER_LENGTH = 300;
	let visualizerData = Array(VISUALIZER_BUFFER_LENGTH).fill(0);

	// RMS calculations (same pattern as VoiceRecording)
	const calculateRMS = (data: Uint8Array) => {
		let sumSquares = 0;
		for (let i = 0; i < data.length; i++) {
			const normalizedValue = (data[i] - 128) / 128;
			sumSquares += normalizedValue * normalizedValue;
		}
		return Math.sqrt(sumSquares / data.length);
	};

	const normalizeRMS = (rms: number) => {
		rms = rms * 10;
		const exp = 1.5;
		const scaledRMS = Math.pow(rms, exp);
		return Math.min(1.0, Math.max(0.01, scaledRMS));
	};

	let animFrameId: number | null = null;

	const analyseAudio = (audioStream: MediaStream) => {
		const audioContext = new AudioContext();
		const audioStreamSource = audioContext.createMediaStreamSource(audioStream);
		const analyser = audioContext.createAnalyser();
		analyser.minDecibels = MIN_DECIBELS;
		audioStreamSource.connect(analyser);

		const timeDomainData = new Uint8Array(analyser.fftSize);

		const processFrame = () => {
			if (state === 'idle' && !recording) return;

			if (state === 'recording') {
				analyser.getByteTimeDomainData(timeDomainData);
				const rmsLevel = calculateRMS(timeDomainData);
				visualizerData.push(normalizeRMS(rmsLevel));
				if (visualizerData.length >= VISUALIZER_BUFFER_LENGTH) {
					visualizerData.shift();
				}
				visualizerData = visualizerData;
			} else if (state === 'paused') {
				// Push flat line while paused
				visualizerData.push(0.01);
				if (visualizerData.length >= VISUALIZER_BUFFER_LENGTH) {
					visualizerData.shift();
				}
				visualizerData = visualizerData;
			}

			animFrameId = window.requestAnimationFrame(processFrame);
		};

		animFrameId = window.requestAnimationFrame(processFrame);
	};

	const startDurationCounter = () => {
		durationCounter = setInterval(() => {
			durationSeconds++;
		}, 1000);
	};

	const stopDurationCounter = () => {
		if (durationCounter) {
			clearInterval(durationCounter);
			durationCounter = null;
		}
	};

	const formatSeconds = (seconds: number) => {
		const hrs = Math.floor(seconds / 3600);
		const mins = Math.floor((seconds % 3600) / 60);
		const secs = seconds % 60;
		const formattedMins = mins < 10 ? `0${mins}` : `${mins}`;
		const formattedSecs = secs < 10 ? `0${secs}` : `${secs}`;
		if (hrs > 0) {
			const formattedHrs = hrs < 10 ? `0${hrs}` : `${hrs}`;
			return `${formattedHrs}:${formattedMins}:${formattedSecs}`;
		}
		return `${formattedMins}:${formattedSecs}`;
	};

	const blobToFile = (blob: Blob, filename: string): File => {
		return new File([blob], filename, { type: blob.type });
	};

	$: if (recording && state === 'idle') {
		startRecording();
	}

	const startRecording = async () => {
		loading = true;
		audioChunks = [];

		try {
			stream = await navigator.mediaDevices.getUserMedia({
				audio: {
					echoCancellation,
					noiseSuppression,
					autoGainControl
				}
			});
		} catch (err) {
			console.error('Error accessing media devices.', err);
			toast.error($i18n.t('Error accessing media devices.'));
			loading = false;
			recording = false;
			return;
		}

		const mimeTypes = ['audio/webm; codecs=opus', 'audio/mp4'];
		const supportedMimeType = mimeTypes.find((type) => MediaRecorder.isTypeSupported(type));

		mediaRecorder = new MediaRecorder(stream, {
			mimeType: supportedMimeType
		});

		mediaRecorder.onstart = () => {
			loading = false;
			state = 'recording';
			startDurationCounter();
			analyseAudio(stream!);
		};

		mediaRecorder.ondataavailable = (event) => {
			if (event.data.size > 0) {
				audioChunks.push(event.data);
				// Track the actual mime type from the first chunk
				if (!observedMimeType && event.data.type) {
					observedMimeType = event.data.type;
				}
			}
		};

		mediaRecorder.onstop = async () => {
			console.log('Meeting recording stopped');

			if (audioChunks.length === 0) {
				toast.error($i18n.t('No audio data recorded.'));
				cleanup();
				return;
			}

			let type = observedMimeType || mediaRecorder?.mimeType || 'audio/webm';
			let ext = type.split('/')[1]?.split(';')[0] || 'webm';
			if (!type.startsWith('audio/')) {
				ext = 'webm';
			}

			const audioBlob = new Blob(audioChunks, { type });
			const filename = `Meeting-Recording-${dayjs().format('YYYY-MM-DD-HHmmss')}.${ext}`;
			const file = blobToFile(audioBlob, filename);

			onComplete(file);
			cleanup();
		};

		// Use timeslice to periodically flush chunks (every 10s) — protects against tab crashes
		try {
			mediaRecorder.start(10000);
		} catch (error) {
			console.error('Error starting recording:', error);
			toast.error($i18n.t('Error starting recording.'));
			loading = false;
			recording = false;
			return;
		}
	};

	const pauseRecording = () => {
		if (mediaRecorder && mediaRecorder.state === 'recording') {
			mediaRecorder.pause();
			state = 'paused';
			stopDurationCounter();
		}
	};

	const resumeRecording = () => {
		if (mediaRecorder && mediaRecorder.state === 'paused') {
			mediaRecorder.resume();
			state = 'recording';
			startDurationCounter();
		}
	};

	const stopRecording = () => {
		if (mediaRecorder && (mediaRecorder.state === 'recording' || mediaRecorder.state === 'paused')) {
			loading = true;
			stopDurationCounter();
			mediaRecorder.stop();
		}
	};

	const cancelRecording = () => {
		if (mediaRecorder && mediaRecorder.state !== 'inactive') {
			// Clear chunks before stopping so onstop doesn't process
			audioChunks = [];
			mediaRecorder.stop();
		}
		cleanup();
		onCancel();
	};

	const cleanup = () => {
		stopDurationCounter();
		if (animFrameId) {
			cancelAnimationFrame(animFrameId);
			animFrameId = null;
		}
		if (stream) {
			stream.getTracks().forEach((track) => track.stop());
			stream = null;
		}
		audioChunks = [];
		visualizerData = Array(VISUALIZER_BUFFER_LENGTH).fill(0);
		durationSeconds = 0;
		state = 'idle';
		recording = false;
		loading = false;
		observedMimeType = '';
	};

	const handleKeyDown = (e: KeyboardEvent) => {
		if (e.key === 'Escape') {
			e.preventDefault();
			cancelRecording();
		}
	};

	let resizeObserver: ResizeObserver;
	let containerWidth: number;
	let maxVisibleItems = 300;
	$: maxVisibleItems = Math.floor(containerWidth / 5);

	onMount(() => {
		window.addEventListener('keydown', handleKeyDown);
		resizeObserver = new ResizeObserver(() => {
			VISUALIZER_BUFFER_LENGTH = Math.floor(window.innerWidth / 4);
			if (visualizerData.length > VISUALIZER_BUFFER_LENGTH) {
				visualizerData = visualizerData.slice(visualizerData.length - VISUALIZER_BUFFER_LENGTH);
			} else {
				visualizerData = Array(VISUALIZER_BUFFER_LENGTH - visualizerData.length)
					.fill(0)
					.concat(visualizerData);
			}
		});
		resizeObserver.observe(document.body);
	});

	onDestroy(() => {
		window.removeEventListener('keydown', handleKeyDown);
		if (resizeObserver) resizeObserver.disconnect();
		// Ensure everything is cleaned up if component is destroyed while recording
		if (state !== 'idle') {
			cleanup();
		}
	});
</script>

<div
	bind:clientWidth={containerWidth}
	class="{loading
		? 'bg-gray-100/50 dark:bg-gray-850/50'
		: state === 'paused'
			? 'bg-amber-300/10 dark:bg-amber-500/10'
			: 'bg-red-300/10 dark:bg-red-500/10'} rounded-full flex justify-between {className}"
>
	<!-- Cancel button (left) -->
	<div class="flex items-center mr-1">
		<button
			type="button"
			class="p-1.5 {loading
				? 'bg-gray-200 dark:bg-gray-700/50'
				: 'bg-red-400/20 text-red-600 dark:text-red-300'} rounded-full"
			on:click={cancelRecording}
			aria-label="Cancel recording"
		>
			<XMark className={'size-4'} />
		</button>
	</div>

	<!-- Waveform visualizer (center) -->
	<div
		class="flex flex-1 self-center items-center justify-between ml-2 mx-1 overflow-hidden h-6"
		dir="rtl"
	>
		<div
			class="flex items-center gap-0.5 h-6 w-full max-w-full overflow-hidden overflow-x-hidden flex-wrap"
		>
			{#each visualizerData.slice().reverse() as rms}
				<div class="flex items-center h-full">
					<div
						class="w-[2px] shrink-0 {loading
							? 'bg-gray-500 dark:bg-gray-400'
							: state === 'paused'
								? 'bg-amber-500 dark:bg-amber-400'
								: 'bg-red-500 dark:bg-red-400'} inline-block h-full"
						style="height: {Math.min(100, Math.max(14, rms * 100))}%;"
					/>
				</div>
			{/each}
		</div>
	</div>

	<!-- Duration + controls (right) -->
	<div class="flex items-center gap-1">
		<!-- Duration -->
		<div class="mx-1.5 pr-1 flex justify-center items-center">
			<div
				class="text-sm {loading
					? 'text-gray-500 dark:text-gray-400'
					: state === 'paused'
						? 'text-amber-500'
						: 'text-red-500'} font-medium flex-1 mx-auto text-center"
			>
				{formatSeconds(durationSeconds)}
			</div>
		</div>

		{#if loading}
			<!-- Loading spinner -->
			<div class="text-gray-500 rounded-full cursor-not-allowed p-1.5">
				<svg
					width="20"
					height="20"
					viewBox="0 0 24 24"
					xmlns="http://www.w3.org/2000/svg"
					fill="currentColor"
				>
					<style>
						.meeting-spinner {
							transform-origin: center;
							animation: meeting-spin 0.75s step-end infinite;
						}
						@keyframes meeting-spin {
							8.3% { transform: rotate(30deg); }
							16.6% { transform: rotate(60deg); }
							25% { transform: rotate(90deg); }
							33.3% { transform: rotate(120deg); }
							41.6% { transform: rotate(150deg); }
							50% { transform: rotate(180deg); }
							58.3% { transform: rotate(210deg); }
							66.6% { transform: rotate(240deg); }
							75% { transform: rotate(270deg); }
							83.3% { transform: rotate(300deg); }
							91.6% { transform: rotate(330deg); }
							100% { transform: rotate(360deg); }
						}
					</style>
					<g class="meeting-spinner">
						<rect x="11" y="1" width="2" height="5" opacity=".14" />
						<rect x="11" y="1" width="2" height="5" transform="rotate(30 12 12)" opacity=".29" />
						<rect x="11" y="1" width="2" height="5" transform="rotate(60 12 12)" opacity=".43" />
						<rect x="11" y="1" width="2" height="5" transform="rotate(90 12 12)" opacity=".57" />
						<rect x="11" y="1" width="2" height="5" transform="rotate(120 12 12)" opacity=".71" />
						<rect x="11" y="1" width="2" height="5" transform="rotate(150 12 12)" opacity=".86" />
						<rect x="11" y="1" width="2" height="5" transform="rotate(180 12 12)" />
					</g>
				</svg>
			</div>
		{:else}
			<!-- Pause / Resume button -->
			<div class="flex items-center">
				{#if state === 'recording'}
					<button
						type="button"
						class="p-1.5 bg-amber-400/20 text-amber-600 dark:text-amber-300 rounded-full hover:bg-amber-400/30 transition"
						on:click={pauseRecording}
						aria-label="Pause recording"
						data-testid="pause-recording-button"
					>
						<!-- Pause icon -->
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 20 20"
							fill="currentColor"
							class="size-4"
						>
							<path
								fill-rule="evenodd"
								d="M6.75 5.25a.75.75 0 0 1 .75.75v8a.75.75 0 0 1-1.5 0V6a.75.75 0 0 1 .75-.75Zm6.5 0a.75.75 0 0 1 .75.75v8a.75.75 0 0 1-1.5 0V6a.75.75 0 0 1 .75-.75Z"
								clip-rule="evenodd"
							/>
						</svg>
					</button>
				{:else if state === 'paused'}
					<button
						type="button"
						class="p-1.5 bg-green-400/20 text-green-600 dark:text-green-300 rounded-full hover:bg-green-400/30 transition"
						on:click={resumeRecording}
						aria-label="Resume recording"
						data-testid="resume-recording-button"
					>
						<!-- Play/Resume icon -->
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 20 20"
							fill="currentColor"
							class="size-4"
						>
							<path d="M6.3 2.841A1.5 1.5 0 004 4.11V15.89a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z" />
						</svg>
					</button>
				{/if}
			</div>

			<!-- Stop button -->
			<div class="flex items-center">
				<button
					id="stop-meeting-recording-button"
					type="button"
					class="p-1.5 bg-red-500 text-white rounded-full hover:bg-red-600 transition"
					on:click={stopRecording}
					aria-label="Stop recording"
					data-testid="stop-recording-button"
				>
					<!-- Stop icon (square) -->
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 20 20"
						fill="currentColor"
						class="size-4"
					>
						<rect x="5" y="5" width="10" height="10" rx="1" />
					</svg>
				</button>
			</div>
		{/if}
	</div>
</div>
