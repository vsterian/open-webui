import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import MeetingRecording from './MeetingRecording.svelte';

// --- Mocks ---

// Mock i18n context
vi.mock('svelte', async (importOriginal) => {
	const actual = (await importOriginal()) as any;
	return {
		...actual,
		getContext: (key: string) => {
			if (key === 'i18n') {
				// The store value needs to be an i18n-like object with a .t() method
				// In Svelte, $i18n auto-subscribes, so the value fn(val) is what $i18n becomes
				const i18nValue = { t: (text: string) => text };
				const store = {
					subscribe: (fn: Function) => {
						fn(i18nValue);
						return () => {};
					}
				};
				return store;
			}
			return actual.getContext(key);
		}
	};
});

// Mock svelte-sonner
vi.mock('svelte-sonner', () => ({
	toast: {
		error: vi.fn(),
		success: vi.fn(),
		warning: vi.fn()
	}
}));

// Mock dayjs
vi.mock('dayjs', () => {
	const dayjs = () => ({
		format: () => '2026-03-16-120000'
	});
	dayjs.extend = () => {};
	return { default: dayjs };
});

vi.mock('dayjs/plugin/localizedFormat', () => ({ default: {} }));

// MediaRecorder mock
class MockMediaRecorder {
	state: string = 'inactive';
	mimeType: string = 'audio/webm; codecs=opus';
	onstart: (() => void) | null = null;
	ondataavailable: ((e: any) => void) | null = null;
	onstop: (() => void) | null = null;

	private _startTimeslice: number | undefined;

	static isTypeSupported(type: string) {
		return type === 'audio/webm; codecs=opus';
	}

	start(timeslice?: number) {
		this._startTimeslice = timeslice;
		this.state = 'recording';
		setTimeout(() => this.onstart?.(), 0);
	}

	stop() {
		this.state = 'inactive';
		setTimeout(() => this.onstop?.(), 0);
	}

	pause() {
		if (this.state === 'recording') {
			this.state = 'paused';
		}
	}

	resume() {
		if (this.state === 'paused') {
			this.state = 'recording';
		}
	}

	// Helper to simulate data available event
	simulateData(blob: Blob) {
		this.ondataavailable?.({ data: blob });
	}
}

// AudioContext mock
class MockAudioContext {
	createMediaStreamSource() {
		return {
			connect: vi.fn()
		};
	}
	createAnalyser() {
		return {
			minDecibels: -45,
			frequencyBinCount: 128,
			fftSize: 256,
			getByteTimeDomainData: vi.fn((arr: Uint8Array) => {
				// Fill with silence (128 = zero crossing)
				arr.fill(128);
			}),
			getByteFrequencyData: vi.fn((arr: Uint8Array) => {
				arr.fill(0);
			}),
			connect: vi.fn()
		};
	}
}

// Setup global mocks
let mockMediaRecorderInstance: MockMediaRecorder | null = null;
let mockStream: any;

function setupGlobalMocks() {
	mockStream = {
		getTracks: () => [{ stop: vi.fn() }],
		getAudioTracks: () => [{ stop: vi.fn() }]
	};

	// getUserMedia mock
	Object.defineProperty(global.navigator, 'mediaDevices', {
		value: {
			getUserMedia: vi.fn().mockResolvedValue(mockStream)
		},
		writable: true,
		configurable: true
	});

	// MediaRecorder mock
	(global as any).MediaRecorder = class extends MockMediaRecorder {
		constructor(stream: any, options: any) {
			super();
			if (options?.mimeType) {
				this.mimeType = options.mimeType;
			}
			mockMediaRecorderInstance = this;
		}
	};
	(global as any).MediaRecorder.isTypeSupported = MockMediaRecorder.isTypeSupported;

	// AudioContext mock
	(global as any).AudioContext = MockAudioContext;

	// requestAnimationFrame mock
	(global as any).requestAnimationFrame = vi.fn((cb) => {
		// Don't call cb to avoid infinite loops in tests
		return 1;
	});
	(global as any).cancelAnimationFrame = vi.fn();

	// ResizeObserver mock
	(global as any).ResizeObserver = class {
		observe = vi.fn();
		unobserve = vi.fn();
		disconnect = vi.fn();
	};
}

describe('MeetingRecording', () => {
	beforeEach(() => {
		setupGlobalMocks();
		mockMediaRecorderInstance = null;
		vi.useFakeTimers();
	});

	afterEach(() => {
		cleanup();
		vi.useRealTimers();
		vi.restoreAllMocks();
	});

	it('renders when recording is true', async () => {
		render(MeetingRecording, {
			props: { recording: true }
		});

		// Wait for startRecording to complete (getUserMedia + MediaRecorder.start)
		await vi.advanceTimersByTimeAsync(50);

		// Should show the stop button
		const stopBtn = screen.getByTestId('stop-recording-button');
		expect(stopBtn).toBeInTheDocument();
	});

	it('shows pause button in recording state', async () => {
		render(MeetingRecording, {
			props: { recording: true }
		});

		await vi.advanceTimersByTimeAsync(50);

		const pauseBtn = screen.getByTestId('pause-recording-button');
		expect(pauseBtn).toBeInTheDocument();
	});

	it('shows resume button after pausing', async () => {
		render(MeetingRecording, {
			props: { recording: true }
		});

		await vi.advanceTimersByTimeAsync(50);

		// Click pause
		const pauseBtn = screen.getByTestId('pause-recording-button');
		await fireEvent.click(pauseBtn);

		// MediaRecorder should now be paused
		expect(mockMediaRecorderInstance?.state).toBe('paused');

		// Resume button should appear
		const resumeBtn = screen.getByTestId('resume-recording-button');
		expect(resumeBtn).toBeInTheDocument();
	});

	it('resumes after pause', async () => {
		render(MeetingRecording, {
			props: { recording: true }
		});

		await vi.advanceTimersByTimeAsync(50);

		// Pause
		const pauseBtn = screen.getByTestId('pause-recording-button');
		await fireEvent.click(pauseBtn);

		expect(mockMediaRecorderInstance?.state).toBe('paused');

		// Resume
		const resumeBtn = screen.getByTestId('resume-recording-button');
		await fireEvent.click(resumeBtn);

		expect(mockMediaRecorderInstance?.state).toBe('recording');
	});

	it('calls onCancel when cancel button is clicked', async () => {
		const onCancel = vi.fn();
		render(MeetingRecording, {
			props: { recording: true, onCancel }
		});

		await vi.advanceTimersByTimeAsync(50);

		// Click cancel (X button)
		const cancelBtn = screen.getByLabelText('Cancel recording');
		await fireEvent.click(cancelBtn);

		expect(onCancel).toHaveBeenCalledOnce();
	});

	it('calls onComplete with a File when stopped after recording', async () => {
		const onComplete = vi.fn();
		render(MeetingRecording, {
			props: { recording: true, onComplete }
		});

		await vi.advanceTimersByTimeAsync(50);

		// Simulate some audio data arriving
		const testBlob = new Blob(['test-audio-data'], { type: 'audio/webm; codecs=opus' });
		mockMediaRecorderInstance?.simulateData(testBlob);

		// Click stop
		const stopBtn = screen.getByTestId('stop-recording-button');
		await fireEvent.click(stopBtn);

		// Wait for onstop callback to fire
		await vi.advanceTimersByTimeAsync(50);

		expect(onComplete).toHaveBeenCalledOnce();
		const file = onComplete.mock.calls[0][0];
		expect(file).toBeInstanceOf(File);
		expect(file.name).toMatch(/^Meeting-Recording-.*\.webm$/);
	});

	it('increments duration counter while recording', async () => {
		render(MeetingRecording, {
			props: { recording: true }
		});

		await vi.advanceTimersByTimeAsync(50);

		// Duration should start at 00:00
		expect(screen.getByText('00:00')).toBeInTheDocument();

		// Advance time by 3 seconds (use async to allow Svelte reactivity)
		await vi.advanceTimersByTimeAsync(3000);

		expect(screen.getByText('00:03')).toBeInTheDocument();
	});

	it('pauses duration counter when paused', async () => {
		render(MeetingRecording, {
			props: { recording: true }
		});

		await vi.advanceTimersByTimeAsync(50);

		// Record for 2 seconds
		await vi.advanceTimersByTimeAsync(2000);
		expect(screen.getByText('00:02')).toBeInTheDocument();

		// Pause
		const pauseBtn = screen.getByTestId('pause-recording-button');
		await fireEvent.click(pauseBtn);

		// Advance 3 more seconds
		await vi.advanceTimersByTimeAsync(3000);

		// Duration should still be 00:02 (paused)
		expect(screen.getByText('00:02')).toBeInTheDocument();
	});

	it('resumes duration counter after resume', async () => {
		render(MeetingRecording, {
			props: { recording: true }
		});

		await vi.advanceTimersByTimeAsync(50);

		// Record for 2 seconds
		await vi.advanceTimersByTimeAsync(2000);

		// Pause
		const pauseBtn = screen.getByTestId('pause-recording-button');
		await fireEvent.click(pauseBtn);

		// Wait 2s while paused
		await vi.advanceTimersByTimeAsync(2000);
		expect(screen.getByText('00:02')).toBeInTheDocument();

		// Resume
		const resumeBtn = screen.getByTestId('resume-recording-button');
		await fireEvent.click(resumeBtn);

		// Record 3 more seconds
		await vi.advanceTimersByTimeAsync(3000);

		// Should be 00:05 (2 + 3, skipping the paused 2)
		expect(screen.getByText('00:05')).toBeInTheDocument();
	});

	it('handles getUserMedia permission denied', async () => {
		const { toast } = await import('svelte-sonner');

		// Override getUserMedia to reject
		(navigator.mediaDevices.getUserMedia as any).mockRejectedValueOnce(
			new Error('Permission denied')
		);

		render(MeetingRecording, {
			props: { recording: true }
		});

		await vi.advanceTimersByTimeAsync(50);

		expect(toast.error).toHaveBeenCalled();
	});

	it('cancels recording on Escape key', async () => {
		const onCancel = vi.fn();
		render(MeetingRecording, {
			props: { recording: true, onCancel }
		});

		await vi.advanceTimersByTimeAsync(50);

		// Press Escape
		await fireEvent.keyDown(window, { key: 'Escape' });

		expect(onCancel).toHaveBeenCalledOnce();
	});

	it('formats hours correctly for long recordings', async () => {
		render(MeetingRecording, {
			props: { recording: true }
		});

		await vi.advanceTimersByTimeAsync(50);

		// Advance by 1 hour, 5 minutes, 30 seconds = 3930 seconds
		await vi.advanceTimersByTimeAsync(3930 * 1000);

		expect(screen.getByText('01:05:30')).toBeInTheDocument();
	});

	it('stops all media tracks on cleanup', async () => {
		const stopFn = vi.fn();
		const mockStreamWithSpy = {
			getTracks: () => [{ stop: stopFn }],
			getAudioTracks: () => [{ stop: stopFn }]
		};
		(navigator.mediaDevices.getUserMedia as any).mockResolvedValueOnce(mockStreamWithSpy);

		const { unmount } = render(MeetingRecording, {
			props: { recording: true }
		});

		await vi.advanceTimersByTimeAsync(50);

		// Unmount while recording
		unmount();

		expect(stopFn).toHaveBeenCalled();
	});
});
