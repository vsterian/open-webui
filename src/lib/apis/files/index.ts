import { WEBUI_API_BASE_URL } from '$lib/constants';
import { splitStream } from '$lib/utils';

const DEFAULT_RESUMABLE_MEDIA_THRESHOLD_BYTES = 64 * 1024 * 1024;
const DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES = 32 * 1024 * 1024;
const AZURE_SERVER_STAGED_PARALLEL_UPLOADS = 6;
const RESUMABLE_PARALLEL_CHUNK_UPLOADS = 3;
const RESUMABLE_CHUNK_RETRY_ATTEMPTS = 3;
const RESUMABLE_RETRY_BACKOFF_MS = 750;

const formatUploadSpeed = (bytesPerSecond: number): string => {
	if (!isFinite(bytesPerSecond) || bytesPerSecond <= 0) return '';
	if (bytesPerSecond >= 1024 * 1024 * 1024) return `${(bytesPerSecond / (1024 * 1024 * 1024)).toFixed(1)} GB/s`;
	if (bytesPerSecond >= 1024 * 1024) return `${(bytesPerSecond / (1024 * 1024)).toFixed(1)} MB/s`;
	if (bytesPerSecond >= 1024) return `${(bytesPerSecond / 1024).toFixed(0)} KB/s`;
	return `${Math.round(bytesPerSecond)} B/s`;
};

const formatETA = (seconds: number): string => {
	if (!isFinite(seconds) || seconds <= 0) return '';
	const s = Math.ceil(seconds);
	if (s < 60) return `~${s}s`;
	const m = Math.floor(s / 60);
	const rem = s % 60;
	if (m < 60) return rem > 0 ? `~${m}m ${rem}s` : `~${m}m`;
	const h = Math.floor(m / 60);
	const remM = m % 60;
	return remM > 0 ? `~${h}h ${remM}m` : `~${h}h`;
};

const buildSpeedSuffix = (bytesUploaded: number, startTime: number): string => {
	const elapsed = (Date.now() - startTime) / 1000;
	if (elapsed <= 0 || bytesUploaded <= 0) return '';
	const speed = bytesUploaded / elapsed;
	const speedStr = formatUploadSpeed(speed);
	if (!speedStr) return '';
	return speedStr;
};

const buildSpeedAndEta = (bytesUploaded: number, totalBytes: number, startTime: number): string => {
	const elapsed = (Date.now() - startTime) / 1000;
	if (elapsed <= 0 || bytesUploaded <= 0) return '';
	const speed = bytesUploaded / elapsed;
	const speedStr = formatUploadSpeed(speed);
	if (!speedStr) return '';
	const remaining = totalBytes - bytesUploaded;
	if (remaining <= 0) return speedStr;
	const eta = formatETA(remaining / speed);
	return eta ? `${speedStr}, ${eta} remaining` : speedStr;
};

type UploadSessionResponse = {
	session_id: string;
	chunk_size: number;
	total_chunks: number;
	upload_mode?: 'azure_sas' | 'azure_server_staged' | 'app_mediated';
	azure_blob_url?: string;
	azure_blob_sas_token?: string;
	azure_blob_x_ms_version?: string;
};

const createUploadSession = async (
	token: string,
	file: File,
	metadata?: object | null,
	process?: boolean | null,
	allowAzureSas = true
): Promise<UploadSessionResponse> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/files/uploads/sessions`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			filename: file.name,
			size: file.size,
			content_type: file.type,
			process: process !== undefined && process !== null ? process : true,
			metadata: metadata || {},
			allow_azure_sas: allowAzureSas
		})
	});

	if (!res.ok) {
		throw await res.json();
	}

	return await res.json();
};

const uploadUploadSessionChunk = async (
	token: string,
	sessionId: string,
	chunkIndex: number,
	chunk: Blob,
	onByteProgress?: (loaded: number, total: number) => void
) => {
	const url = `${WEBUI_API_BASE_URL}/files/uploads/sessions/${sessionId}/chunks/${chunkIndex}`;

	// Use XHR when byte-level progress is requested (single-chunk uploads)
	if (onByteProgress) {
		return new Promise<unknown>((resolve, reject) => {
			const xhr = new XMLHttpRequest();
			xhr.open('PUT', url);
			xhr.setRequestHeader('Accept', 'application/json');
			xhr.setRequestHeader('authorization', `Bearer ${token}`);

			xhr.upload.onprogress = (e) => {
				if (e.lengthComputable) {
					onByteProgress(e.loaded, e.total);
				}
			};

			xhr.onload = () => {
				if (xhr.status >= 200 && xhr.status < 300) {
					try {
						resolve(JSON.parse(xhr.responseText));
					} catch {
						resolve(null);
					}
				} else {
					try {
						reject(JSON.parse(xhr.responseText));
					} catch {
						reject(new Error(`Upload chunk failed: ${xhr.status}`));
					}
				}
			};

			xhr.onerror = () => reject(new Error('Upload chunk network error'));
			xhr.ontimeout = () => reject(new Error('Upload chunk timeout'));
			xhr.send(chunk);
		});
	}

	const res = await fetch(url, {
		method: 'PUT',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		},
		body: chunk
	});

	if (!res.ok) {
		throw await res.json();
	}

	return await res.json();
};

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const uploadSessionChunkWithRetry = async (
	token: string,
	sessionId: string,
	chunkIndex: number,
	chunk: Blob,
	onByteProgress?: (loaded: number, total: number) => void
) => {
	let lastError: unknown = null;

	for (let attempt = 1; attempt <= RESUMABLE_CHUNK_RETRY_ATTEMPTS; attempt++) {
		try {
			return await uploadUploadSessionChunk(token, sessionId, chunkIndex, chunk, onByteProgress);
		} catch (err) {
			lastError = err;
			if (attempt < RESUMABLE_CHUNK_RETRY_ATTEMPTS) {
				await delay(RESUMABLE_RETRY_BACKOFF_MS * attempt);
			}
		}
	}

	throw lastError;
};

const finalizeUploadSession = async (token: string, sessionId: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/files/uploads/sessions/${sessionId}/finalize`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({})
	});

	if (!res.ok) {
		throw await res.json();
	}

	return await res.json();
};

type UploadSessionStatus = {
	session_id: string;
	filename: string;
	chunk_size: number;
	total_chunks: number;
	uploaded_chunks: number;
	uploaded_chunk_indexes: number[];
	uploaded_bytes: number;
	state: string;
};

const getUploadSession = async (token: string, sessionId: string): Promise<UploadSessionStatus> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/files/uploads/sessions/${sessionId}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		throw await res.json();
	}

	return await res.json();
};

const buildAzureBlockId = (chunkIndex: number): string => {
	const raw = `block-${String(chunkIndex).padStart(8, '0')}`;
	return btoa(raw);
};

const normalizeSasToken = (token: string): string => {
	let value = (token || '').trim();
	if (!value) {
		return '';
	}
	const questionMarkIndex = value.indexOf('?');
	if (questionMarkIndex >= 0) {
		value = value.slice(questionMarkIndex + 1);
	}
	return value.replace(/^[?&]+/, '');
};

const stageAzureBlock = async (
	blobUrl: string,
	sasToken: string,
	blockId: string,
	chunk: Blob,
	xMsVersion = '2024-11-04'
) => {
	let response: Response | null = null;
	let lastError: unknown = null;
	const url = `${blobUrl}?comp=block&blockid=${encodeURIComponent(blockId)}&${sasToken}`;

	for (let attempt = 1; attempt <= RESUMABLE_CHUNK_RETRY_ATTEMPTS; attempt++) {
		try {
			response = await fetch(url, {
				method: 'PUT',
				headers: {
					'x-ms-version': xMsVersion
				},
				body: chunk
			});

			if (response.ok) {
				return;
			}

			if (response.status === 401 || response.status === 403 || response.status === 409) {
				throw new Error(`AZURE_DIRECT_UPLOAD_AUTH_${response.status}`);
			}

			lastError = new Error(`Azure block upload failed: ${response.status}`);
		} catch (err) {
			lastError = err;
		}

		if (attempt < RESUMABLE_CHUNK_RETRY_ATTEMPTS) {
			await delay(RESUMABLE_RETRY_BACKOFF_MS * attempt);
		}
	}

	throw lastError;
};

const uploadLargeMediaFileAzureDirect = async (
	token: string,
	file: File,
	session: UploadSessionResponse,
	onProgress?: (progress: string) => void
) => {
	const blobUrl = session.azure_blob_url || '';
	const sasToken = normalizeSasToken(session.azure_blob_sas_token || '');
	if (!blobUrl || !sasToken) {
		throw new Error('AZURE_DIRECT_UPLOAD_MISSING_CONFIGURATION');
	}

	const chunkSize = session.chunk_size || DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES;
	const totalChunks = Math.max(session.total_chunks || 0, Math.ceil(file.size / chunkSize));
	const workerCount = Math.max(1, Math.min(RESUMABLE_PARALLEL_CHUNK_UPLOADS, totalChunks));
	const blockIds = new Array<string>(totalChunks);
	let nextChunkIndex = 0;
	let uploadedChunkCount = 0;
	const uploadStartTime = Date.now();
	let totalBytesConfirmed = 0;

	const uploadWorker = async () => {
		while (true) {
			const currentChunkIndex = nextChunkIndex;
			nextChunkIndex += 1;

			if (currentChunkIndex >= totalChunks) {
				return;
			}

			const start = currentChunkIndex * chunkSize;
			const end = Math.min(start + chunkSize, file.size);
			const chunk = file.slice(start, end);
			const blockId = buildAzureBlockId(currentChunkIndex);

			await stageAzureBlock(blobUrl, sasToken, blockId, chunk, session.azure_blob_x_ms_version || '2024-11-04');
			blockIds[currentChunkIndex] = blockId;

			uploadedChunkCount += 1;
			totalBytesConfirmed += (end - start);
			if (onProgress) {
				const percentage = Math.floor((totalBytesConfirmed / file.size) * 100);
				const speedInfo = buildSpeedAndEta(totalBytesConfirmed, file.size, uploadStartTime);
				const suffix = speedInfo ? ` | ${speedInfo}` : '';
				onProgress(
					`Uploading media (Azure direct)... ${percentage}%${suffix}`
				);
			}
		}
	};

	const workers: Promise<void>[] = [];
	for (let index = 0; index < workerCount; index++) {
		workers.push(uploadWorker());
	}

	await Promise.all(workers);

	if (onProgress) {
		onProgress('Finalizing upload...');
	}

	const res = await fetch(`${WEBUI_API_BASE_URL}/files/uploads/sessions/${session.session_id}/finalize`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			block_ids: blockIds
		})
	});

	if (!res.ok) {
		throw await res.json();
	}

	return await res.json();
};

const uploadLargeMediaFile = async (
	token: string,
	file: File,
	metadata?: object | null,
	process?: boolean | null,
	onProgress?: (progress: string) => void,
	forceAppMediated = false
) => {
	const session = await createUploadSession(token, file, metadata, process, !forceAppMediated);

	if (session.upload_mode === 'azure_sas' && !forceAppMediated) {
		try {
			return await uploadLargeMediaFileAzureDirect(token, file, session, onProgress);
		} catch (error) {
			console.warn('Azure direct upload failed, falling back to app-mediated upload', error);
			if (onProgress) {
				onProgress('Azure direct upload unavailable, retrying with standard upload...');
			}
			return await uploadLargeMediaFile(token, file, metadata, process, onProgress, true);
		}
	}

	// azure_server_staged and app_mediated both send chunks to backend
	const progressLabel =
		session.upload_mode === 'azure_server_staged'
			? 'Uploading media (Azure)...'
			: 'Uploading media...';

	const chunkSize = session.chunk_size || DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES;
	const totalChunks = Math.max(
		session.total_chunks || 0,
		Math.ceil(file.size / chunkSize)
	);

	// Resume support: check for already-uploaded chunks
	const alreadyUploaded = new Set<number>();
	try {
		const sessionStatus = await getUploadSession(token, session.session_id);
		if (sessionStatus.uploaded_chunk_indexes && sessionStatus.uploaded_chunk_indexes.length > 0) {
			for (const idx of sessionStatus.uploaded_chunk_indexes) {
				alreadyUploaded.add(idx);
			}
			console.log(`Resuming upload: ${alreadyUploaded.size}/${totalChunks} chunks already uploaded`);
		}
	} catch {
		// Session status unavailable; upload all chunks from scratch
	}

	let nextChunkIndex = 0;
	let uploadedChunkCount = alreadyUploaded.size;
	const uploadStartTime = Date.now();
	let totalBytesConfirmed = 0;

	// Account for already-uploaded bytes
	for (const idx of alreadyUploaded) {
		const start = idx * chunkSize;
		const end = Math.min(start + chunkSize, file.size);
		totalBytesConfirmed += (end - start);
	}

	// For single-chunk uploads, track in-flight byte progress for real-time updates
	const isSingleChunk = totalChunks === 1;
	let inFlightBytes = 0;

	const maxWorkers =
		session.upload_mode === 'azure_server_staged'
			? AZURE_SERVER_STAGED_PARALLEL_UPLOADS
			: RESUMABLE_PARALLEL_CHUNK_UPLOADS;
	const workerCount = Math.max(
		1,
		Math.min(maxWorkers, totalChunks - alreadyUploaded.size)
	);

	const uploadWorker = async () => {
		while (true) {
			let currentChunkIndex = nextChunkIndex;
			nextChunkIndex += 1;

			// Skip already-uploaded chunks
			while (currentChunkIndex < totalChunks && alreadyUploaded.has(currentChunkIndex)) {
				currentChunkIndex = nextChunkIndex;
				nextChunkIndex += 1;
			}

			if (currentChunkIndex >= totalChunks) {
				return;
			}

			const start = currentChunkIndex * chunkSize;
			const end = Math.min(start + chunkSize, file.size);
			const chunk = file.slice(start, end);

			// Use XHR byte-level progress for single-chunk uploads
			const byteProgressCb = isSingleChunk && onProgress
				? (loaded: number, _total: number) => {
					inFlightBytes = loaded;
					const currentBytes = totalBytesConfirmed + inFlightBytes;
					const percentage = Math.min(99, Math.floor((currentBytes / file.size) * 100));
					const speedInfo = buildSpeedAndEta(currentBytes, file.size, uploadStartTime);
					const suffix = speedInfo ? ` | ${speedInfo}` : '';
					onProgress(`${progressLabel} ${percentage}%${suffix}`);
				}
				: undefined;

			await uploadSessionChunkWithRetry(
				token,
				session.session_id,
				currentChunkIndex,
				chunk,
				byteProgressCb
			);

			inFlightBytes = 0;
			uploadedChunkCount += 1;
			totalBytesConfirmed += (end - start);
			if (onProgress) {
				const percentage = Math.floor((totalBytesConfirmed / file.size) * 100);
				const speedInfo = buildSpeedAndEta(totalBytesConfirmed, file.size, uploadStartTime);
				const suffix = speedInfo ? ` | ${speedInfo}` : '';
				onProgress(`${progressLabel} ${percentage}%${suffix}`);
			}
		}
	};

	const workers: Promise<void>[] = [];
	for (let index = 0; index < workerCount; index++) {
		workers.push(uploadWorker());
	}

	await Promise.all(workers);

	if (onProgress) {
		onProgress('Finalizing upload...');
	}

	return await finalizeUploadSession(token, session.session_id);
};

export const uploadFile = async (
	token: string,
	file: File,
	metadata?: object | null,
	process?: boolean | null,
	onProgress?: (progress: string) => void
) => {
	const data = new FormData();
	data.append('file', file);
	if (metadata) {
		data.append('metadata', JSON.stringify(metadata));
	}

	const searchParams = new URLSearchParams();
	if (process !== undefined && process !== null) {
		searchParams.append('process', String(process));
	}

	let error = null;
	const isMediaFile = file.type.startsWith('video/') || file.type.startsWith('audio/');
	const useResumable = isMediaFile && file.size >= DEFAULT_RESUMABLE_MEDIA_THRESHOLD_BYTES;

	const res = useResumable
		? await uploadLargeMediaFile(token, file, metadata, process, onProgress).catch((err) => {
				error = err?.detail || err?.message || err;
				console.error(err);
				return null;
		  })
		: await fetch(`${WEBUI_API_BASE_URL}/files/?${searchParams.toString()}`, {
				method: 'POST',
				headers: {
					Accept: 'application/json',
					authorization: `Bearer ${token}`
				},
				body: data
		  })
				.then(async (res) => {
					if (!res.ok) throw await res.json();
					return res.json();
				})
				.catch((err) => {
					error = err.detail || err.message;
					console.error(err);
					return null;
				});

	if (error) {
		throw error;
	}

	if (res) {
		const status = await getFileProcessStatus(token, res.id, true);
		let reachedTerminalStatus = false;

		if (status && status.ok && status.body) {
			const reader = status.body
				.pipeThrough(new TextDecoderStream())
				.pipeThrough(splitStream('\n'))
				.getReader();

			while (true) {
				const { value, done } = await reader.read();
				if (done) {
					break;
				}

				try {
					let lines = value.split('\n');

					for (const line of lines) {
						if (line !== '') {
							console.log(line);
							if (line === 'data: [DONE]') {
								console.log(line);
								reachedTerminalStatus = true;
							} else {
								let data = JSON.parse(line.replace(/^data: /, ''));
								console.log(data);

								if (onProgress && data?.progress !== undefined && data.progress !== '') {
									onProgress(data.progress);
								}

								if (data?.error) {
									console.error(data.error);
									res.error = data.error;
								}

								if (data?.status === 'completed' || data?.status === 'failed') {
									reachedTerminalStatus = true;
								}

								if (res?.data) {
									res.data = data;
								}
							}
						}
					}
				} catch (error) {
					console.log(error);
				}
			}
		}

		if (!reachedTerminalStatus) {
			for (let attempt = 0; attempt < 120; attempt++) {
				const fallbackStatus = await getFileProcessStatus(token, res.id, false);
				if (!fallbackStatus?.ok) {
					break;
				}

				const fallbackData = await fallbackStatus.json();
				if (onProgress && fallbackData?.progress) {
					onProgress(fallbackData.progress);
				}
				if (fallbackData?.error) {
					res.error = fallbackData.error;
				}

				if (fallbackData?.status === 'completed' || fallbackData?.status === 'failed') {
					break;
				}

				await new Promise((resolve) => setTimeout(resolve, 2000));
			}
		}
	}

	if (error) {
		throw error;
	}

	return res;
};

export const getFileProcessStatus = async (token: string, id: string, stream = true) => {
	const queryParams = new URLSearchParams();
	queryParams.append('stream', String(stream));

	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/files/${id}/process/status?${queryParams}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
	}).catch((err) => {
		error = err.detail;
		console.error(err);
		return null;
	});

	if (error) {
		throw error;
	}

	return res;
};

export const uploadDir = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/files/upload/dir`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getFiles = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/files/`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const searchFiles = async (
	token: string,
	filename: string = '*',
	skip: number = 0,
	limit: number = 50
) => {
	let error = null;

	const searchParams = new URLSearchParams();
	searchParams.append('filename', filename);
	searchParams.append('skip', String(skip));
	searchParams.append('limit', String(limit));

	const res = await fetch(`${WEBUI_API_BASE_URL}/files/search?${searchParams.toString()}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getFileById = async (token: string, id: string) => {
	if (!id || id === 'null' || id === 'undefined') {
		return null;
	}
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/files/${id}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateFileDataContentById = async (token: string, id: string, content: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/files/${id}/data/content/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			content: content
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getFileContentById = async (id: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/files/${id}/content`, {
		method: 'GET',
		headers: {
			Accept: 'application/json'
		},
		credentials: 'include'
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return await res.arrayBuffer();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);

			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const deleteFileById = async (token: string, id: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/files/${id}`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const deleteAllFiles = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/files/all`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
