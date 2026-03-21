# Azure Blob Upload Recommendations for Large iPhone Video Files via OpenWebUI

## Goal
Improve upload speed, reliability, and security for files larger than 1 GB uploaded from an iPhone through the OpenWebUI interface hosted in a container on an Azure VM.

## Recommended Architecture

### Best pattern
Use OpenWebUI as the user interface only, but upload the actual video data directly from the iPhone browser to Azure Blob Storage.

Flow:
1. User opens OpenWebUI on iPhone and selects a video.
2. OpenWebUI calls a backend endpoint such as `POST /uploads/session`.
3. The backend uses the Azure VM managed identity via `DefaultAzureCredential`.
4. The backend requests a user delegation key and generates a short-lived user delegation SAS for a single blob.
5. The iPhone browser uploads the file directly to Azure Blob Storage using chunked block blob upload.
6. After all blocks succeed, the browser commits the block list.
7. OpenWebUI notifies the backend with `POST /uploads/complete`.
8. The backend triggers transcription only after upload commit succeeds.

### Why this is the best option
- Faster: removes the Azure VM container from the large-file data path.
- Safer: avoids hardcoded SAS tokens and storage account keys.
- More reliable: uses Azure block blob uploads designed for large files.
- Better for mobile: supports resumable uploads and retry of failed blocks only.

## Main Problems in Current Implementation
Your logs show the backend calling `stage_block` with a request body around 1.6 GB in a single PUT.
That is likely why 600 MB works but 1.6 GB fails with:

`azure.core.exceptions.ServiceResponseError: ('Connection aborted.', TimeoutError('The write operation timed out'))`

This should be replaced with staged block uploads using multiple chunks.

## Implementation Recommendations

### 1. Authentication
- Enable managed identity on the Azure VM hosting OpenWebUI.
- Use `DefaultAzureCredential()` in the backend.
- Do not hardcode SAS tokens in OpenWebUI.
- Generate a user delegation SAS dynamically for each upload session.
- Keep SAS permissions narrow: write-only, single blob, short expiration, HTTPS only.

### 2. Direct browser upload
- Keep OpenWebUI as the upload screen.
- Change the upload action so the browser uploads directly to Blob Storage.
- Do not relay large files through FastAPI or the container for 1 GB+ uploads.
- Use Blob Storage CORS so the browser can upload cross-origin.

### 3. Large-file upload strategy
- Use block blobs.
- Split files into chunks.
- Do not use one giant `stage_block` request.
- Start testing with chunk sizes such as 32 MiB, 64 MiB, and 128 MiB.
- Upload blocks in parallel.
- Start with moderate parallelism, such as 2 to 4 concurrent block uploads.
- Commit the block list only after all blocks are uploaded.
- Store block IDs and upload session state to support resume.
- Retry only failed blocks with exponential backoff.

### 4. Backend endpoints to add
- `POST /uploads/session`
  - Input: filename, content type, size.
  - Output: blob name, container, SAS URL, expiry, block size, concurrency, upload session id.
- `POST /uploads/complete`
  - Input: upload session id, blob name, block list or completion metadata.
  - Action: verify completion and trigger transcription.
- Optional: `GET /uploads/session/{id}`
  - Returns uploaded block indexes for resume support.

### 5. Frontend behavior to add
- Request upload session before uploading.
- Slice the selected video into chunks.
- Upload chunks directly to Azure Blob Storage.
- Track progress per block and total upload progress.
- Retry failed blocks.
- Resume from already uploaded blocks if upload is interrupted.
- Commit block list after all uploads finish.
- Notify backend after successful commit.

## CORS Configuration for Blob Storage
Configure CORS on the Blob service of the storage account.

### Portal
1. Open the storage account in Azure Portal.
2. Go to **Resource sharing (CORS)** or Blob service CORS settings.
3. Add a rule for the OpenWebUI origin.

### Suggested rule
- Allowed origins: `https://your-openwebui-domain`
- Allowed methods: `PUT,OPTIONS,GET,HEAD`
- Allowed headers: `*` initially, then reduce later
- Exposed headers: `*` initially, then reduce later
- Max age: `3600`

### CLI example
```bash
az storage account blob-service-properties cors-rule add   --account-name <storage-account>   --resource-group <resource-group>   --allowed-origins "https://your-openwebui-domain"   --allowed-methods PUT OPTIONS GET HEAD   --allowed-headers "*"   --exposed-headers "*"   --max-age 3600
```

## Azure RBAC / Identity Checklist
- Enable system-assigned or user-assigned managed identity on the VM.
- Grant blob data access to the target storage account or container.
- Ensure the identity can request a user delegation key.
- Keep permissions scoped as narrowly as practical.

## Suggested First Configuration
Use this as the first implementation baseline:
- Direct browser upload from iPhone to Blob Storage.
- User delegation SAS generated by backend.
- Managed identity on VM for backend authentication.
- Block size: 64 MiB.
- Parallel uploads: 2 to 4.
- Resumable upload session with block tracking.
- Exponential backoff retries.
- Final commit after all blocks succeed.
- Transcription starts only after blob commit.

## Operational Metrics to Track
- Total upload time.
- Average block upload time.
- Failed block count.
- Retry count.
- Time to commit block list.
- SAS expiry failures.
- Upload success rate for files above 1 GB.
- Comparison by network type: Wi-Fi vs mobile.

## Things to Remove from Current Design
- Hardcoded SAS in application config.
- Uploading the full file through the OpenWebUI backend for large files.
- Single `stage_block` request with the entire file body.
- Starting transcription before blob commit is confirmed.

## Final Step-by-Step Plan
1. Enable managed identity on the Azure VM.
2. Assign required Blob Storage RBAC permissions.
3. Add backend code using `DefaultAzureCredential()`.
4. Add backend endpoint to generate user delegation SAS.
5. Configure Blob Storage CORS for the OpenWebUI origin.
6. Modify OpenWebUI upload flow to request upload session first.
7. Implement chunked block upload in the browser.
8. Add parallel uploads with controlled concurrency.
9. Add resume and retry support.
10. Commit the block list after all blocks upload successfully.
11. Notify backend on completion.
12. Trigger transcription only after successful completion.
13. Test with files above 1 GB on iPhone over Wi-Fi and mobile networks.
14. Tune chunk size and concurrency based on observed performance.
