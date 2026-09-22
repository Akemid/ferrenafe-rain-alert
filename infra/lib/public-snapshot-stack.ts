import { Stack, StackProps, RemovalPolicy, CfnOutput } from 'aws-cdk-lib/core';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';

/**
 * One S3 bucket holding one public object: the rain-alert cycle's snapshot
 * (`status.json`), read by the public status page.
 *
 * See design D-`public-status-page` and
 * `docs/runbooks/public-page-deploy.md` for the deploy procedure and the
 * verification steps this stack's policy is meant to satisfy.
 */
export class PublicSnapshotStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    const bucket = new s3.Bucket(this, 'PublicSnapshot', {
      // Amplify's reverse proxy fetches server-side and does NOT sign the
      // request, so the object must be publicly readable. The rewrite hides
      // it from the browser; it does not protect it. Acceptable because
      // this bucket holds one file whose contents are exactly what the page
      // displays, and nothing else ever goes in here.
      blockPublicAccess: new s3.BlockPublicAccess({
        blockPublicAcls: true,
        ignorePublicAcls: true,
        blockPublicPolicy: false,
        restrictPublicBuckets: false,
      }),
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    bucket.addToResourcePolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject'],
        principals: [new iam.AnyPrincipal()],
        // One key. Not the bucket.
        resources: [bucket.arnForObjects('status.json')],
      }),
    );

    new CfnOutput(this, 'SnapshotBucketName', { value: bucket.bucketName });
    new CfnOutput(this, 'SnapshotObjectUrl', { value: bucket.urlForObject('status.json') });
  }
}
